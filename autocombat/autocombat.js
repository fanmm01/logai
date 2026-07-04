// ==UserScript==
// @name         半自动战斗扩展
// @author       fanmm
// @version      0.7.0
// @description  CoC魔法少女半自动战斗规则扩展。支持.setab 0(最小自动化)与.setab 1(完全自动化)。
//               前置：loganalyser/logutil v5.0+ 与 coc7扩展。
//               v0.7.0: 召唤物战斗系统、领域效果、DOT持续伤害、Buff/Debuff应用、护盾吸收、吸血、HP阈值触发。
// @timestamp    1759931652
// @diceRequireVer 1.4.0
// @license      MIT
// ==/UserScript==

// ============================================================
//  扩展注册
// ============================================================
let ext = seal.ext.find('autocombat');
if (!ext) {
  ext = seal.ext.new('autocombat', 'fanmm', '0.6.0');
  seal.ext.register(ext);
}

// ── 扩展配置项 ──
seal.ext.registerStringConfig(ext, "PvP战斗后端地址", "http://127.0.0.1:8889", "Python 战斗引擎后端地址（battle_http_server.py），用于 .setab 2 模式。");

// 模式通过 .setab / .setrestim 指令切换，使用 $g 变量存储

// ============================================================
//  规则模板注册（使 .set autocombat / .set mg 可用）
// ============================================================
(function registerTemplate() {
  const template = {
    name: 'autocombat',
    fullName: '魔法少女半自动战斗规则',
    authors: ['fanmm'],
    version: '0.7.0',
    updatedTime: '20260701',
    templateVer: '1.0',
    setConfig: {
      diceSides: 100,
      enableTip: '已启用魔法少女半自动战斗规则 (autocombat v0.5.0)',
      keys: ['autocombat', '魔法少女', 'mg', 'battleauto'],
      relatedExt: ['coc7'],
    },
    nameTemplate: {
      autocombat: {
        template: '{$t玩家_RAW} HP{体力}/{体力上限} MP{魔力}/{魔力上限} SAN{理智}',
        helpText: '魔法少女战斗名片',
      },
    },
    attrConfig: {
      top: ['体力', '魔力', '理智', '等级', '魔法少女序号'],
      sortBy: 'name',
      ignores: [],
      showAs: {
        体力: '{体力}/{体力上限}',
        魔力: '{魔力}/{魔力上限}',
      },
      setter: null,
    },
    defaults: {
      等级: 1,
      魔法少女序号: 0,
      回合行动数: 1,
    },
    defaultsComputed: {},
    alias: {
      体力: ['hp', 'HP'],
      体力上限: ['hpmax', 'HPMAX'],
      魔力: ['mp', 'MP'],
      魔力上限: ['mpmax', 'MPMAX'],
      理智: ['san', 'SAN'],
      伤害值: ['dmg', 'DMG'],
      伤害贯穿: ['pen', 'PEN'],
      致死骰: ['leth', 'LETH'],
      可反击: ['counterable'],
      等级: ['level', 'LV'],
      魔法少女序号: ['mgid'],
      回合行动数: ['acts'],
    },
    textMap: {},
    textMapHelpInfo: null,
  };
  try {
    seal.gameSystem.newTemplate(JSON.stringify(template));
    console.log('[autocombat] 规则模板已注册（.set autocombat / .set mg / .set 魔法少女 / .set battleauto）');
  } catch (e) {
    console.log('[autocombat] 规则模板注册失败（可能已存在）:', e);
  }
})();

// ============================================================
//  Helper functions
// ============================================================

/** Roll a dice expression (e.g. "1d4+1d6") and return integer result */
function rollDice(ctx, expr) {
  if (!expr || expr === '0') return 0;
  const result = seal.format(ctx, `{${expr}}`);
  const num = parseInt(result);
  return isNaN(num) ? 0 : num;
}

/** Roll a single d10 via seal.format, returning 1-10 (10 → 0 for COC digits). */
function rollD10(ctx) {
  const s = seal.format(ctx, '{1d10}');
  const n = parseInt(s);
  if (isNaN(n)) return 0;
  return n === 10 ? 0 : n; // 10→0 for COC d100 digit arithmetic
}

/** Roll D100 with optional bonus/penalty dice, using manual d10 rolls
 *  to avoid seal.format returning unparseable descriptive strings.
 *
 *  COC7 rule: a bonus die rolls an extra tens die, take the lower → better.
 *             a penalty die rolls an extra tens die, take the higher → worse.
 *  00 (0×10+0) is read as 100.
 *
 *  bpStr: '' | 'b' | 'p' | 'b2' | 'p2' | 'b3' etc.
 *  Returns { result, detail } where detail shows the extra tens values,
 *    e.g. "奖励4" or "惩罚8,3".
 */
function rollD100(ctx, bpStr) {
  if (!bpStr) {
    // Simple roll: one d100 as two d10 digits
    const tens = rollD10(ctx);
    const units = rollD10(ctx);
    const r = (tens === 0 && units === 0) ? 100 : tens * 10 + units;
    return { result: r, detail: '' };
  }
  const m = bpStr.match(/^([bp])(\d*)$/i);
  if (!m) {
    const tens = rollD10(ctx);
    const units = rollD10(ctx);
    const r = (tens === 0 && units === 0) ? 100 : tens * 10 + units;
    return { result: r, detail: '' };
  }
  const type = m[1].toLowerCase(); // 'b' or 'p'
  const count = m[2] ? parseInt(m[2]) : 1;

  // Roll the base tens & units
  let bestTens = rollD10(ctx);
  const units = rollD10(ctx);

  // Roll N extra tens dice
  const extraTens = [];
  for (let i = 0; i < count; i++) {
    extraTens.push(rollD10(ctx));
  }

  // Apply bonus (take lowest tens) or penalty (take highest tens)
  for (const t of extraTens) {
    if (type === 'b') {
      if (t < bestTens) bestTens = t;
    } else {
      if (t > bestTens) bestTens = t;
    }
  }

  const r = (bestTens === 0 && units === 0) ? 100 : bestTens * 10 + units;
  const label = type === 'b' ? '奖励' : '惩罚';
  // Show all extra tens values: e.g. "奖励4" or "奖励2,7"
  const extraStr = extraTens.join(',');
  return { result: r, detail: `${label}${extraStr}` };
}

/** Calculate net bonus/penalty dice from all buffs with stacking (max ±3).
 *
 *  Counts all bonus dice and penalty dice from:
 *  - AUX 16 (伤害成功率奖励惩罚) buffs with auxVal like 'b','b2','b3','p','p2','p3'
 *  - Skill-specific bonus/penalty dice (技能奖励骰/技能惩罚骰) matching skillName
 *  - The passed-in bpSuffix (from command like .btab/.btap)
 *
 *  Nets them: bonus − penalty, capped at ±3.
 *  Returns formatted string: 'b3','b2','b','','p','p2','p3'
 */
function calcNetBp(buffs, bpSuffix, skillName) {
  let bonusCount = 0;
  let penaltyCount = 0;

  // 1. Parse bpSuffix (from command)
  if (bpSuffix) {
    const m = bpSuffix.match(/^([bp])(\d*)$/i);
    if (m) {
      const cnt = m[2] ? parseInt(m[2]) : 1;
      if (m[1].toLowerCase() === 'b') {
        bonusCount += cnt;
      } else {
        penaltyCount += cnt;
      }
    }
  }

  // 2. Scan all buffs
  for (const b of buffs) {
    // AUX 16: 伤害成功率奖励惩罚
    if (b.auxCode === 16 && b.auxVal) {
      const val = String(b.auxVal).trim().toLowerCase();
      const m = val.match(/^([bp])(\d*)$/);
      if (m) {
        const cnt = m[2] ? parseInt(m[2]) : 1;
        if (m[1] === 'b') {
          bonusCount += cnt;
        } else {
          penaltyCount += cnt;
        }
      }
    }
    // Skill-specific bonus/penalty dice
    if (skillName) {
      try {
        const auxType = b.auxType || '';
        const auxVal = String(b.auxVal || '').trim();
        if (auxType === '技能奖励骰' && auxVal.toLowerCase() === skillName.toLowerCase()) {
          bonusCount += 1;
        }
        if (auxType === '技能惩罚骰' && auxVal.toLowerCase() === skillName.toLowerCase()) {
          penaltyCount += 1;
        }
      } catch (e) { /* ignore */ }
    }
  }

  // 3. Net and cap at ±3
  let net = bonusCount - penaltyCount;
  net = Math.max(-3, Math.min(3, net));

  if (net > 0) {
    return net > 1 ? 'b' + net : 'b';
  } else if (net < 0) {
    const n = -net;
    return n > 1 ? 'p' + n : 'p';
  } else {
    return '';
  }
}

/** COC7 success rank (rule 2: domestic common).
 *  大失败=-2  失败=-1  成功=1  困难成功=2  极难成功=3  大成功=4
 */
function successRank(d100, skillVal) {
  if (skillVal <= 0) return d100 <= 5 ? 4 : (d100 >= 96 ? -2 : -1);
  if (d100 === 1) return 4;
  if (d100 === 100) return -2;
  if (d100 > skillVal) {
    if (skillVal < 50 && d100 >= 96) return -2;
    return -1;
  }
  let rank = 1;
  if (d100 <= skillVal / 5) rank = 3;
  else if (d100 <= skillVal / 2) rank = 2;
  if (d100 <= 5) rank = 4;
  return rank;
}

/** Get human-readable success rank text */
function rankText(ctx, rank) {
  switch (rank) {
    case -2: return '大失败';
    case -1: return '失败';
    case  1: return '成功';
    case  2: return '困难成功';
    case  3: return '极难成功';
    case  4: return '大成功';
    default: return '';
  }
}

/** Get attribute value from character card */
function getAttr(ctx, name, fallback) {
  fallback = (fallback !== undefined) ? fallback : 0;
  const vr = seal.vars.intGet(ctx, name);
  if (vr[1] && vr[0] > 0) return vr[0];
  const result = seal.format(ctx, `{${name}}`);
  const n = parseInt(result);
  return isNaN(n) ? fallback : n;
}

/** Get string attribute */
function getStrAttr(ctx, name) {
  const vr = seal.vars.strGet(ctx, name);
  return vr[1] ? vr[0] : '';
}

/** Parse skill name that may have compound format:
 *   "50"        → raw number
 *   "斗殴"       → skill name
 *   "斗殴50"     → skill with value
 *   "斗殴+10"    → skill with positive modifier
 *   "斗殴-5"     → skill with negative modifier
 *   "斗殴a"      → suffixed skill (falls back to "斗殴" if not found)
 */
function parseSkillValue(ctx, rawName) {
  // Pure number
  const asNum = parseInt(rawName);
  if (!isNaN(asNum) && String(asNum) === rawName) {
    return { name: String(asNum), value: asNum, isRaw: true };
  }
  // "skill+N" or "skill-N"
  const pmMatch = rawName.match(/^(\D+?)([+-]\d+)$/);
  if (pmMatch) {
    const base = pmMatch[1];
    const mod = parseInt(pmMatch[2]);
    const baseVal = getAttr(ctx, base);
    if (baseVal > 0) return { name: base, value: Math.max(0, baseVal + mod) };
    return { name: base, value: Math.max(0, 50 + mod) };
  }
  // "skill50" compound (no sign) — number replaces the skill value
  const compound = rawName.match(/^(\D+?)(\d+)$/);
  if (compound) {
    const base = compound[1];
    const num = parseInt(compound[2]);
    const baseVal = getAttr(ctx, base);
    if (baseVal > 0) return { name: base, value: num };
    if (num >= 5 && num <= 99) {
      const skillOnly = rawName.replace(/\d+$/, '');
      const sv = getAttr(ctx, skillOnly);
      if (sv > 0) return { name: skillOnly, value: sv };
      return { name: skillOnly || String(num), value: num };
    }
    return { name: base || String(num), value: Math.max(baseVal, num) };
  }
  // Plain skill name (possibly suffixed with a/b)
  let sv = getAttr(ctx, rawName);
  if (sv > 0) return { name: rawName, value: sv };
  // Fallback: if suffixed, try without suffix
  if (/[ab]$/.test(rawName) && rawName.length > 1) {
    const baseName = rawName.slice(0, -1);
    sv = getAttr(ctx, baseName);
    if (sv > 0) return { name: baseName, value: sv };
  }
  return { name: rawName, value: sv };
}

/** Normalize damage dice string: "d6"→"1d6" */
function normalizeDice(expr) {
  if (!expr) return '1d4';
  if (/^\d+$/.test(expr)) return expr;
  return expr.replace(/(^|[^0-9])d/gi, '$11d');
}

/** Get weapon suffix from a skill name (trailing 'a' or 'b') */
function getWeaponSuffix(name) {
  if (/[ab]$/.test(name) && name.length > 1) return name.slice(-1);
  return '';
}

// ============================================================
//  Combat skill classification — derived from the COC skill table
//  (logstat&enAll.c 战斗类技能)
// ============================================================

/** All combat-class skills (战斗类技能) from the COC skill table */
const ALL_COMBAT_SKILLS = [
  '格斗', '斗殴', '斧', '链锯', '连枷', '绞索', '矛', '剑', '鞭',
  '射击', '射击:弓', '射击:手枪', '射击:重武器', '射击:火焰喷射器',
  '射击:机枪', '射击:步霰', '射击:冲锋枪',
  '投掷', '爆破', '炮术',
];

/** Melee combat skills (近战战斗类技能) — used for default counter-attack selection */
const MELEE_COMBAT_SKILLS = [
  '格斗', '斗殴', '斧', '链锯', '连枷', '绞索', '矛', '剑', '鞭',
];

/** Ranged combat skills (远程/射击/投掷/爆破/炮术) — cannot be reacted to */
const RANGED_COMBAT_SKILLS = [
  '射击', '射击:弓', '射击:手枪', '射击:重武器', '射击:火焰喷射器',
  '射击:机枪', '射击:步霰', '射击:冲锋枪',
  '投掷', '爆破', '炮术',
];

/** Common aliases for ranged skills (without "射击:" prefix) */
const RANGED_ALIASES = [
  '手枪', '弓', '弓箭', '弓术', '重武器', '火焰喷射器',
  '机枪', '步枪', '霰弹枪', '散弹枪', '步霰', '冲锋枪',
  '炮术', '火炮', '火器', '枪械',
];

/** Check if a skill name refers to a ranged weapon (cannot be dodged/countered) */
function isRangedSkill(name) {
  if (RANGED_COMBAT_SKILLS.includes(name)) return true;
  if (RANGED_ALIASES.includes(name)) return true;
  if (/^射击/.test(name)) return true;  // "射击:xxx" or bare "射击"
  return false;
}

/** Find the highest-valued melee combat skill on a character card.
 *  Returns { name, value } — fallback to 斗殴/25 if nothing found.
 */
function getBestMeleeSkill(ctx) {
  let bestSkill = '斗殴';
  let bestVal = 0;
  for (const ms of MELEE_COMBAT_SKILLS) {
    const sv = getAttr(ctx, ms);
    if (sv > bestVal) { bestVal = sv; bestSkill = ms; }
  }
  return { name: bestSkill, value: Math.max(bestVal, 25) };
}

/** Parse a dice expression into signed terms like ["1d6", "+2", "-1d4"].
 *  Simple match-based approach — no exec() loop, no g-flag issues.
 */
function parseDiceTerms(diceExpr) {
  if (!diceExpr) return [];
  // Match: optional sign, optional whitespace, then either NdS or a plain number
  const terms = diceExpr.match(/[+-]?\s*\d*d\d+|[+-]?\s*\d+/gi);
  return terms || [];
}

/** Calculate expected (average) damage. */
function avgDamage(diceExpr) {
  if (!diceExpr) return 0;
  let total = 0;
  const terms = parseDiceTerms(diceExpr);
  for (const term of terms) {
    const sign = term.trim()[0] === '-' ? -1 : 1;
    const clean = term.replace(/[+-]\s*/, '');
    const m = clean.match(/^(\d*)d(\d+)$/i);
    if (m) {
      const cnt = m[1] ? parseInt(m[1], 10) : 1;
      const sides = parseInt(m[2], 10);
      total += sign * cnt * (sides + 1) / 2;
    } else {
      total += sign * (parseInt(clean, 10) || 0);
    }
  }
  return total;
}

/** Calculate maximum possible damage. */
function maxDamage(diceExpr) {
  if (!diceExpr) return 0;
  let total = 0;
  const terms = parseDiceTerms(diceExpr);
  for (const term of terms) {
    const sign = term.trim()[0] === '-' ? -1 : 1;
    const clean = term.replace(/[+-]\s*/, '');
    const m = clean.match(/^(\d*)d(\d+)$/i);
    if (m) {
      const cnt = m[1] ? parseInt(m[1], 10) : 1;
      total += sign * cnt * parseInt(m[2], 10);
    } else {
      total += sign * (parseInt(clean, 10) || 0);
    }
  }
  return Math.max(0, total);
}

/** Roll damage normally */
function rollDmgNormal(ctx, diceExpr) {
  const val = rollDice(ctx, diceExpr);
  return { value: val, detail: `${diceExpr}=${val}` };
}

/** Roll damage with advantage (twice, take higher) */
function rollDmgAdv(ctx, diceExpr) {
  const r1 = rollDice(ctx, diceExpr);
  const r2 = rollDice(ctx, diceExpr);
  return { value: Math.max(r1, r2), detail: `优势(${diceExpr}=${r1}, ${r2}) → ${Math.max(r1, r2)}` };
}

/** Roll damage with disadvantage (twice, take lower) */
function rollDmgDisadv(ctx, diceExpr) {
  const r1 = rollDice(ctx, diceExpr);
  const r2 = rollDice(ctx, diceExpr);
  return { value: Math.min(r1, r2), detail: `劣势(${diceExpr}=${r1}, ${r2}) → ${Math.min(r1, r2)}` };
}

/** Get DB string from build value */
function dbFromBuild(build) {
  const table = { '-2': '-2', '-1': '-1d4', '0': '0', '1': '1d4', '2': '1d6', '3': '2d6', '4': '3d6', '5': '4d6' };
  return table[String(build)] || '0';
}

/** Check if a concatenated timing string contains a specific code.
 *  Timing format (spec): "1"=被动, "2"=主动作, "3"=附加动作, "4"=反应.
 *  Multiple timings are concatenated: e.g. "23" = 主动作+附加动作.
 */
function hasTiming(timingStr, code) {
  if (!timingStr) return false;
  return String(timingStr).indexOf(String(code)) !== -1;
}

/** Check if a concatenated 客体 string contains a specific target code.
 *  Format: same as timing — e.g. "14" = 自身(1)+敌方单体(4).
 */
function hasObject(objStr, code) {
  if (!objStr) return false;
  return String(objStr).indexOf(String(code)) !== -1;
}

/** Determine if a context represents an NPC (has no character card attributes). */
function isNPCContext(ctx) {
  const keyAttrs = ['力量', '体质', '敏捷', '智力', '意志', '外貌', '教育', '体型', '幸运'];
  const aliasAttrs = ['hp', 'san', 'mp', 'str', 'con', 'dex', 'int', 'pow', 'app', 'siz', 'edu', 'luc'];
  let found = 0;
  for (const a of keyAttrs) {
    const vr = seal.vars.intGet(ctx, a);
    if (vr[1] && vr[0] > 0) found++;
  }
  for (const a of aliasAttrs) {
    const vr = seal.vars.intGet(ctx, a);
    if (vr[1] && vr[0] > 0) found++;
  }
  return found < 2;
}

// ============================================================
//  Combat HP temp store — defers HP changes until .unh
// ============================================================
function combatHPKey(groupId) { return `combat_hp_${groupId}`; }
function getCombatHP(groupId) {
  const raw = ext.storageGet(combatHPKey(groupId));
  if (!raw) return {};
  try { return JSON.parse(raw); } catch (e) { return {}; }
}
function setCombatHP(groupId, data) {
  ext.storageSet(combatHPKey(groupId), JSON.stringify(data));
}

/** Initialize combat HP for both parties if not already tracked.
 *  .st changes are synced via the onCommandReceived hook. */
function initCombatHP(groupId, atkUserId, atkCtx, defUserId, defCtx) {
  const hp = getCombatHP(groupId);
  if (hp[atkUserId] === undefined) {
    const v = seal.vars.intGet(atkCtx, '体力');
    hp[atkUserId] = v[1] ? v[0] : 10;
  }
  if (hp[defUserId] === undefined) {
    const v = seal.vars.intGet(defCtx, '体力');
    hp[defUserId] = v[1] ? v[0] : 10;
  }
  setCombatHP(groupId, hp);
}

/** Write combat HP back to character card on .unh */
function flushCombatHP(groupId, userId, ctx) {
  const hp = getCombatHP(groupId);
  if (hp[userId] !== undefined) {
    seal.vars.intSet(ctx, '体力', hp[userId]);
    delete hp[userId];
    setCombatHP(groupId, hp);
  }
}

/** Get active buffs for a user from combat effects.
 *  Returns array of buff objects with {skillMod, auxType, auxCode, auxVal, attrMod}. */
function getActiveBuffs(groupId, userId) {
  const raw = ext.storageGet(`combat_effects_${groupId}`);
  if (!raw) return [];
  let effects;
  try { effects = JSON.parse(raw); } catch(e) { return []; }
  return effects.filter(eff =>
    (eff.type === 'buff' || eff.type === 'debuff') &&
    eff.targetUserId === userId &&
    eff.remainingRounds !== 0  // 0=expired (will be cleaned up), -1=persistent
  );
}

/** Get total active shield value for a user. Returns {total, effects[]}. */
function getActiveShields(groupId, userId) {
  const raw = ext.storageGet(`combat_effects_${groupId}`);
  if (!raw) return { total: 0, effects: [] };
  let effects;
  try { effects = JSON.parse(raw); } catch(e) { return { total: 0, effects: [] }; }
  const shields = effects.filter(eff =>
    eff.type === 'shield' &&
    eff.targetUserId === userId &&
    eff.remainingRounds !== 0
  );
  const total = shields.reduce((sum, s) => sum + (s.value || 0), 0);
  return { total, effects: shields };
}

/** Absorb damage with shields, reducing shield values first.
 *  Returns {remainingDamage, absorbedByShield, shieldMsgs[]}. */
function absorbDamageWithShield(groupId, userId, dmgValue) {
  if (dmgValue <= 0) return { remainingDamage: 0, absorbedByShield: 0, shieldMsgs: [] };
  const raw = ext.storageGet(`combat_effects_${groupId}`);
  if (!raw) return { remainingDamage: dmgValue, absorbedByShield: 0, shieldMsgs: [] };
  let effects;
  try { effects = JSON.parse(raw); } catch(e) { return { remainingDamage: dmgValue, absorbedByShield: 0, shieldMsgs: [] }; }
  let remaining = dmgValue;
  let absorbed = 0;
  let msgs = [];
  for (const eff of effects) {
    if (eff.type !== 'shield' || eff.targetUserId !== userId || eff.remainingRounds === 0) continue;
    if (remaining <= 0) break;
    const shieldVal = eff.value || 0;
    if (shieldVal <= 0) continue;
    const absorb = Math.min(remaining, shieldVal);
    remaining -= absorb;
    absorbed += absorb;
    eff.value -= absorb;
    if (eff.value <= 0) {
      eff.remainingRounds = 0;  // mark expired
      msgs.push(`护盾【${eff.spellName || ''}】被击破！`);
    } else {
      msgs.push(`护盾【${eff.spellName || ''}】吸收 ${absorb} 点伤害（剩余 ${eff.value}）`);
    }
  }
  ext.storageSet(`combat_effects_${groupId}`, JSON.stringify(effects));
  return { remainingDamage: remaining, absorbedByShield: absorbed, shieldMsgs: msgs };
}

/** Apply skill modifier from active buffs. Returns adjusted skill value. */
function applyBuffSkillMod(groupId, userId, baseSkillVal) {
  const buffs = getActiveBuffs(groupId, userId);
  let adjusted = baseSkillVal;
  for (const b of buffs) {
    // AUX 15: 伤害成功率加减 → numeric modifier to skill value
    if (b.auxCode === 15 && b.auxVal !== undefined && b.auxVal !== '') {
      const mod = parseInt(b.auxVal);
      if (!isNaN(mod)) adjusted += mod;
    }
    // Legacy: skillMod field (from 技能加减值) may contain format like "斗殴+10"
    if (b.skillMod) {
      const m = String(b.skillMod).match(/([+-]?\d+)$/);
      if (m) adjusted += parseInt(m[1]);
    }
  }
  return Math.max(1, adjusted);
}

/** Check HP thresholds and fire triggers. Called during tick-down.
 *  Returns array of trigger messages. */
function checkHpThresholds(groupId, initList) {
  const msgs = [];
  const effRaw = ext.storageGet(`combat_effects_${groupId}`);
  if (!effRaw) return msgs;
  let effects;
  try { effects = JSON.parse(effRaw); } catch(e) { return msgs; }

  for (const entry of initList) {
    const userId = entry.userId;
    const hpStore = getCombatHP(groupId);
    const curHP = hpStore[userId];
    if (curHP === undefined) continue;
    // Find max HP (stored in init entry or effects)
    const maxHP = entry.maxHp || entry.hp || curHP;
    if (maxHP <= 0) continue;
    const ratio = curHP / maxHP;

    // Check hpTrigger effects for this user
    for (const eff of effects) {
      if (eff.type !== 'hpTrigger' || eff.targetUserId !== userId) continue;
      if (eff.triggered) continue;
      const threshold = (eff.triggerRatio || 0.5);
      if (ratio <= threshold) {
        eff.triggered = true;
        msgs.push(`⚠️ 【${entry.name || userId}】生命值降至 ${Math.floor(ratio*100)}%（阈值${Math.floor(threshold*100)}%），触发【${eff.spellName || ''}】！`);
        if (eff.triggerSpellIndex) {
          msgs.push(`  → 自动释放技能${eff.triggerSpellIndex}`);
        }
      }
    }
  }
  if (msgs.length > 0) {
    ext.storageSet(`combat_effects_${groupId}`, JSON.stringify(effects));
  }
  return msgs;
}

// ============================================================
//  Damage value calculator — rolls dice, applies to combat HP store,
//  returns the numeric damage dealt (without building output text).
// ============================================================
function calcDamageValue(diceCtx, loserUserId, winnerUserId, groupId,
                         winnerRank, winnerRoll, dmgDice, pen, leth,
                         dmgAdv, dmgDis) {
  const dice = dmgDice || '1d4';
  const mx = maxDamage(dice);
  let dmgValue = 0;

  switch (winnerRank) {
    case 2: { const r = rollDmgAdv(diceCtx, dmgDice); dmgValue = r.value; break; }
    case 3: {
      if (pen) { const extra = rollDmgNormal(diceCtx, dmgDice); dmgValue = mx + extra.value; }
      else { dmgValue = mx; }
      break;
    }
    case 4: {
      if (winnerRoll === 1) { dmgValue = mx * 2; }
      else if (pen) { dmgValue = mx * 2; }
      else { const extra = rollDmgNormal(diceCtx, dmgDice); dmgValue = mx + extra.value; }
      break;
    }
    default: {
      if (dmgAdv) { const r = rollDmgAdv(diceCtx, dmgDice); dmgValue = r.value; }
      else if (dmgDis) { const r = rollDmgDisadv(diceCtx, dmgDice); dmgValue = r.value; }
      else { const r = rollDmgNormal(diceCtx, dmgDice); dmgValue = r.value; }
      break;
    }
  }

  // Apply to combat HP store — shields absorb first
  const hpStore = getCombatHP(groupId);
  let curHP = hpStore[loserUserId];
  if (curHP === undefined) curHP = 10;

  // Shield absorption
  const shieldResult = absorbDamageWithShield(groupId, loserUserId, dmgValue);
  let effectiveDmg = shieldResult.remainingDamage;

  const expDmg = avgDamage(dice);
  if (leth && expDmg > 6) {
    const lethDie = Math.max(2, curHP * 2);
    const lethRoll = parseInt(seal.format(diceCtx, `{1d${lethDie}}`), 10) || 0;
    const lethThreshold = Math.floor(expDmg);
    if (lethRoll <= lethThreshold) {
      curHP = 0;
    } else {
      curHP = Math.max(0, Math.floor(curHP - effectiveDmg));
    }
  } else {
    curHP = Math.max(0, Math.floor(curHP - effectiveDmg));
  }

  hpStore[loserUserId] = curHP;
  setCombatHP(groupId, hpStore);
  return dmgValue;
}

/** Same as calcDamageValue but also writes the new HP back to the loser's card.
 *  loserCtx must be the loser's own context (for seal.vars.intSet). */
function calcDamageAndSyncCard(loserCtx, diceCtx, loserUserId, winnerUserId, groupId,
                                winnerRank, winnerRoll, dmgDice, pen, leth, dmgAdv, dmgDis) {
  const dmg = calcDamageValue(diceCtx, loserUserId, winnerUserId, groupId,
    winnerRank, winnerRoll, dmgDice, pen, leth, dmgAdv, dmgDis);
  // Sync store→card so .st reads correct current HP
  const hpStore = getCombatHP(groupId);
  const curHP = hpStore[loserUserId];
  if (curHP !== undefined && loserCtx && loserCtx.player) {
    seal.vars.intSet(loserCtx, '体力', curHP);
  }
  return dmg;
}

// ============================================================
//  Unified damage resolution — uses combat HP store (with output text)
// ============================================================
function resolveDamage(diceCtx, loserUserId, winnerUserId, groupId,
                       winnerRank, winnerRoll, dmgDice, pen, leth, dmgAdv, dmgDis,
                       loserName, winnerName) {
  let out = '';
  let dmgValue = 0, dmgDetail = '';
  const dice = dmgDice || '1d4';
  const mx = maxDamage(dice);

  switch (winnerRank) {
    case 2: { const r = rollDmgAdv(diceCtx, dmgDice); dmgValue = r.value; dmgDetail = r.detail; break; }
    case 3: {
      if (pen) { const extra = rollDmgNormal(diceCtx, dmgDice); dmgValue = mx + extra.value; dmgDetail = `贯穿！满值${mx}+${dmgDice}=${extra.value} = ${dmgValue}`; }
      else { dmgValue = mx; dmgDetail = `${dmgDice}满值=${mx}`; }
      break;
    }
    case 4: {
      if (winnerRoll === 1) { dmgValue = mx * 2; dmgDetail = `大成功！${dmgDice}满值${mx}×2=${dmgValue}`; }
      else if (pen) { dmgValue = mx * 2; dmgDetail = `贯穿！${dmgDice}满值${mx}×2=${dmgValue}`; }
      else { const extra = rollDmgNormal(diceCtx, dmgDice); dmgValue = mx + extra.value; dmgDetail = `${dmgDice}满值${mx}+${extra.value} = ${dmgValue}`; }
      break;
    }
    default: {
      if (dmgAdv) { const r = rollDmgAdv(diceCtx, dmgDice); dmgValue = r.value; dmgDetail = r.detail; }
      else if (dmgDis) { const r = rollDmgDisadv(diceCtx, dmgDice); dmgValue = r.value; dmgDetail = r.detail; }
      else { const r = rollDmgNormal(diceCtx, dmgDice); dmgValue = r.value; dmgDetail = r.detail; }
      break;
    }
  }

  out += `${loserName} 受到伤害为：\n`;
  out += `${dmgDetail} = ${dmgValue}\n`;

  // Read HP from combat store
  const hpStore = getCombatHP(groupId);
  let curHP = hpStore[loserUserId];
  if (curHP === undefined) curHP = 10;

  // Lethality check (no NPC restriction)
  const expDmg = avgDamage(dice);
  if (leth && expDmg > 6) {
    const lethDie = Math.max(2, curHP * 2);
    const lethRoll = parseInt(seal.format(diceCtx, `{1d${lethDie}}`), 10) || 0;
    const lethThreshold = Math.floor(expDmg);
    out += `\n致死骰情况为：1d${lethDie} = ${lethRoll} `;
    if (lethRoll <= lethThreshold) {
      out += `<= ${lethThreshold}，成功，战斗结束\n`;
      curHP = 0;
    } else {
      out += `> ${lethThreshold}，失败，战斗继续\n`;
      curHP = Math.max(0, Math.floor(curHP - dmgValue));
    }
  } else {
    curHP = Math.max(0, Math.floor(curHP - dmgValue));
  }

  // Write HP back to combat store
  hpStore[loserUserId] = curHP;
  setCombatHP(groupId, hpStore);

  // Display both parties' HP
  const winnerHP = hpStore[winnerUserId];
  out += `${loserName}的剩余生命值为：${curHP}\n`;
  if (winnerHP !== undefined) {
    out += `${winnerName}的当前生命值为：${winnerHP}\n`;
  }

  return out;
}

// ============================================================
//  .setab <0|1|2>  — 切换自动化程度
// ============================================================
const cmdSetab = seal.ext.newCmdItemInfo();
cmdSetab.name = 'setab';
cmdSetab.help =
  '.setab 0 // 最小自动化模式（仅单次攻击-反应-结算 + 变身/解除）\n' +
  '.setab 1 // 完全自动化模式（默认，含地图、回合制等）\n' +
  '.setab 2 // Python后端完全自动化模式（需启动 battle_http_server.py）';
cmdSetab.solve = (ctx, msg, cmdArgs) => {
  const val = cmdArgs.getArgN(1);
  switch (val) {
    case '0':
      seal.vars.intSet(ctx, '$gAutoBattleMode', 0);
      seal.replyToSender(ctx, msg,
        '已切换至【半自动战斗·最小自动化模式】(.setab 0)\n' +
        '支持指令：.bta start(战斗前同步) .bta(攻击) .e(反应) .bta end(战斗后同步) .hs(变身) .unh(解除变身) .setrestim(公布时机)');
      break;
    case '1':
      seal.vars.intSet(ctx, '$gAutoBattleMode', 1);
      seal.replyToSender(ctx, msg,
        '已切换至【半自动战斗·完全自动化模式】(.setab 1)\n' +
        '（完全自动化模式尚在开发中）');
      break;
    case '2':
      seal.vars.intSet(ctx, '$gAutoBattleMode', 2);
      seal.replyToSender(ctx, msg,
        '已切换至【半自动战斗·Python后端模式】(.setab 2)\n' +
        '需确保 battle_http_server.py 已启动（端口8889）。\n' +
        '使用 .btastartfull2 开始PvP战斗，或 .btastartfullai <编号> 开始PvE战斗。');
      break;
    case '':
    case 'help': {
      const ret = seal.ext.newCmdExecuteResult(true);
      ret.showHelp = true;
      return ret;
    }
    default:
      seal.replyToSender(ctx, msg, '用法：.setab 0（最小自动化）/ .setab 1（完全自动化）/ .setab 2（Python后端）');
  }
  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  .setrestim <0|1>  — 检定结果公布时机
// ============================================================
const cmdSetrestim = seal.ext.newCmdItemInfo();
cmdSetrestim.name = 'setrestim';
cmdSetrestim.help =
  '.setrestim 0 // 攻击时立即公布检定结果（默认）\n' +
  '.setrestim 1 // 反应动作后才一并公布双方检定结果';
cmdSetrestim.solve = (ctx, msg, cmdArgs) => {
  const val = cmdArgs.getArgN(1);
  switch (val) {
    case '0':
      seal.vars.intSet(ctx, '$gRestimMode', 0);
      seal.replyToSender(ctx, msg, '已切换：攻击时立即公布检定结果。');
      break;
    case '1':
      seal.vars.intSet(ctx, '$gRestimMode', 1);
      seal.replyToSender(ctx, msg, '已切换：反应动作后才一并公布双方检定结果。');
      break;
    default:
      seal.replyToSender(ctx, msg, '用法：.setrestim 0（立即公布） 或 .setrestim 1（延迟公布）');
  }
  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  .bta start  — 战斗开始前同步属性快照
//  Saves a snapshot of all combat-relevant attributes and skills,
//  marks combat as active so .st changes are tracked in real time.
// ============================================================
const cmdBtaStart = seal.ext.newCmdItemInfo();
cmdBtaStart.name = 'bta start';
cmdBtaStart.help =
  '.bta start // 战斗前同步全部战斗属性快照\n' +
  '保存当前所有战斗类技能与属性的值，标记战斗进行中。\n' +
  '战斗期间 .st 修改的属性将实时反映至战斗内。';
cmdBtaStart.solve = (ctx, msg, cmdArgs) => {
  const mctx = seal.getCtxProxyFirst(ctx, cmdArgs) || ctx;

  // Collect all combat-relevant attributes & skills
  const snapshot = {};

  // Base attributes
  const baseAttrs = ['力量','体型','体质','敏捷','外貌','教育','智力','意志','幸运'];
  for (const a of baseAttrs) {
    const v = seal.vars.intGet(mctx, a);
    snapshot[a] = v[1] ? v[0] : 0;
  }

  // All combat skills
  for (const s of ALL_COMBAT_SKILLS) {
    const v = seal.vars.intGet(mctx, s);
    snapshot[s] = v[1] ? v[0] : 0;
  }

  // Extra combat-relevant stats
  snapshot['闪避'] = getAttr(mctx, '闪避');
  snapshot['体格'] = getAttr(mctx, '体格', 0);
  snapshot['行动力'] = getAttr(mctx, '行动力') || getAttr(mctx, 'mov', 8);
  snapshot['体力'] = getAttr(mctx, '体力', 10);
  snapshot['体力上限'] = getAttr(mctx, '体力上限', 10);
  snapshot['魔力'] = getAttr(mctx, '魔力') || getAttr(mctx, 'mp', 10);
  snapshot['魔力上限'] = getAttr(mctx, '魔力上限') || getAttr(mctx, 'mp', 10);
  snapshot['理智'] = getAttr(mctx, '理智') || getAttr(mctx, 'san', 50);
  snapshot['伤害值'] = getStrAttr(mctx, '伤害值');
  snapshot['伤害贯穿'] = (seal.vars.intGet(mctx, '伤害贯穿'))[1] ? (seal.vars.intGet(mctx, '伤害贯穿'))[0] : undefined;
  snapshot['致死骰'] = (seal.vars.intGet(mctx, '致死骰'))[1] ? (seal.vars.intGet(mctx, '致死骰'))[0] : undefined;

  const key = `bta_snapshot_${mctx.player.userId}`;
  ext.storageSet(key, JSON.stringify(snapshot));

  // Mark combat as active
  seal.vars.intSet(mctx, '$gCombatActive', 1);

  // Init combat HP
  const groupId = mctx.group ? mctx.group.groupId : 'private';
  initCombatHP(groupId, mctx.player.userId, mctx, mctx.player.userId, mctx);

  const pn = seal.format(mctx, '{$t玩家}');
  let out = `${pn} 的战斗属性已同步！\n`;
  out += `战斗标记已开启，.st 修改的属性将实时生效。\n`;
  out += `已同步属性：\n`;
  out += `力量${snapshot['力量']} 体型${snapshot['体型']} 体质${snapshot['体质']} 敏捷${snapshot['敏捷']}\n`;
  out += `斗殴${snapshot['斗殴']} 闪避${snapshot['闪避']} 格斗${snapshot['格斗']}\n`;
  out += `HP ${snapshot['体力']}/${snapshot['体力上限']}  MP ${snapshot['魔力']}/${snapshot['魔力上限']}  SAN ${snapshot['理智']}`;
  seal.replyToSender(ctx, msg, out);
  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  .bta end  — 战斗结束后完成同步
//  Flushes combat HP, clears pending attacks, restores state.
// ============================================================
const cmdBtaEnd = seal.ext.newCmdItemInfo();
cmdBtaEnd.name = 'bta end';
cmdBtaEnd.help =
  '.bta end // 战斗结束后完成属性同步\n' +
  '将战斗HP写回人物卡，清除待处理攻击，关闭战斗标记。';
cmdBtaEnd.solve = (ctx, msg, cmdArgs) => {
  const mctx = seal.getCtxProxyFirst(ctx, cmdArgs) || ctx;
  const gid = mctx.group ? mctx.group.groupId : 'private';

  // --- .setab 2 HTTP path: clean up backend battle instance ---
  const autoMode2 = getAutoMode(ctx);
  const battleId2 = ext.storageGet(`pvp_battle_${gid}`);
  if (autoMode2 >= 2 && battleId2) {
    pvpFetch(`/api/pvp/${battleId2}/end`, {
      player_id: mctx.player.userId,
    }).then(_result => {
      ext.storageSet(`pvp_battle_${gid}`, '');
      ext.storageSet(`pvp_human_${gid}`, '');
      // Also do local cleanup
      flushCombatHP(gid, mctx.player.userId, mctx);
      const targetsKey = `pending_targets_${gid}`;
      ext.storageSet(targetsKey, '');
      ext.storageSet(`pending_atk_${gid}`, '');
      const snapKey = `bta_snapshot_${mctx.player.userId}`;
      ext.storageSet(snapKey, '');
      seal.vars.intSet(mctx, '$gCombatActive', 0);
      const pn = seal.format(mctx, '{$t玩家}');
      seal.replyToSender(ctx, msg, `${pn} 的战斗已结束（后端战斗实例已清理），属性已同步回人物卡。`);
    });
    return seal.ext.newCmdExecuteResult(true);
  }

  // Flush combat HP back to character card
  const groupId = gid;
  flushCombatHP(groupId, mctx.player.userId, mctx);

  // Clear all pending attacks for this group (per-user keys + tracking list)
  const targetsKey = `pending_targets_${groupId}`;
  const targetsRaw = ext.storageGet(targetsKey);
  if (targetsRaw) {
    try {
      const tids = JSON.parse(targetsRaw);
      for (const tid of tids) {
        ext.storageSet(`pending_atk_${groupId}_${tid}`, '');
      }
    } catch (e) {}
    ext.storageSet(targetsKey, '');
  }
  // Also clear old-format key for backward compat
  ext.storageSet(`pending_atk_${groupId}`, '');

  // Clear snapshot
  const snapKey = `bta_snapshot_${mctx.player.userId}`;
  ext.storageSet(snapKey, '');

  // Mark combat as inactive
  seal.vars.intSet(mctx, '$gCombatActive', 0);

  const pn = seal.format(mctx, '{$t玩家}');
  seal.replyToSender(ctx, msg, `${pn} 的战斗已结束，属性已同步回人物卡。`);
  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  .stb <+/-value> @target  — 战斗内修改HP
//  Reads current combat HP, applies delta, writes back.
//  Examples: .stb -5 @target  /  .stb 10 @target  /  .stb +3
// ============================================================
const cmdStb = seal.ext.newCmdItemInfo();
cmdStb.name = 'stb';
cmdStb.help =
  '.stb <值> @目标 // 战斗内直接修改HP\n' +
  '.stb +5  // 回复5点HP\n' +
  '.stb -3  // 扣除3点HP\n' +
  '.stb 10  // 将HP设为10';
cmdStb.allowDelegate = true;
cmdStb.solve = (ctx, msg, cmdArgs) => {
  const mctx = seal.getCtxProxyFirst(ctx, cmdArgs) || ctx;
  const args = cmdArgs.cleanArgs.split(/\s+/).filter(a => a.length > 0);
  if (args.length < 1) {
    seal.replyToSender(ctx, msg, '用法：.stb <值> 或 .stb +<值> 或 .stb -<值> @目标');
    return seal.ext.newCmdExecuteResult(true);
  }

  const valStr = args[0];
  const groupId = mctx.group ? mctx.group.groupId : 'private';
  const hpStore = getCombatHP(groupId);
  let curHP = hpStore[mctx.player.userId];
  if (curHP === undefined) {
    // Not in combat store yet — init from card
    const cv = seal.vars.intGet(mctx, '体力');
    curHP = cv[1] ? cv[0] : 10;
  }

  let newHP;
  if (valStr.startsWith('+')) {
    newHP = curHP + (parseInt(valStr.slice(1)) || 0);
  } else if (valStr.startsWith('-')) {
    newHP = curHP - (parseInt(valStr.slice(1)) || 0);
  } else {
    newHP = parseInt(valStr) || curHP;
  }
  newHP = Math.max(0, newHP);

  hpStore[mctx.player.userId] = newHP;
  setCombatHP(groupId, hpStore);

  const pn = seal.format(mctx, '{$t玩家}');
  const delta = newHP - curHP;
  const deltaStr = delta >= 0 ? `+${delta}` : `${delta}`;
  seal.replyToSender(ctx, msg,
    `${pn} 战斗HP: ${curHP} → ${newHP} (${deltaStr})`);
  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  .bta / .btab / .btap  — 攻击指令
//  Syntax: .bta [skill] [damage_dice] [penetration 0/1] [lethality 0/1] @target [/a/b/c]
// ============================================================
function makeBtaCmd(baseName) {
  const cmd = seal.ext.newCmdItemInfo();
  cmd.name = baseName;
  cmd.help =
    `.bta <技能> [伤害骰] [贯穿0/1] [致死0/1] [n#] @目标1 [/a/b/c] [@目标2 ...]\n` +
    `.bta asa 斗殴 ...  // 全局后缀模式（所有技能查找使用后缀a）\n` +
    `n# 表示攻击次数（如 3# = 连击3次），可放在任意位置\n` +
    `支持多目标：@某某1 /a @某某2 /b（每个目标独立获得反应提示）\n` +
    `近战技能 → 等待目标 .e 反应（一次反应应对全部攻击）；远程技能 → 直接结算\n` +
    `--- kp 指令 ---\n` +
    `.bta setreaction <编号> d/c  // 设定怪物默认反应\n` +
    `.bta new <模板名>  // 新建战斗模板\n.bta map [宽*高]  // 创建/查看地图\n` +
    `.bta add <人物> <坐标>  // 放置人物\n.bta save / import <名>  // 保存/载入\n` +
    `.btab ...  // 奖励骰\n` +
    `.btap ...  // 惩罚骰\n` +
    `.btab2 ... // 2个奖励骰（以此类推）`;
  cmd.allowDelegate = true;  // mctx=@对象, ctx=自身
  cmd.solve = (ctx, msg, cmdArgs) => {
    // --- Check for kp GM sub-commands first (no @target needed) ---
    if (handleKpCommand(ctx, msg, cmdArgs)) {
      return seal.ext.newCmdExecuteResult(true);
    }

    // --- .setab 2 HTTP path: route attack through Python backend ---
    const _gid = ctx.group ? ctx.group.groupId : 'private';
    const _autoMode = getAutoMode(ctx);
    const _battleId = ext.storageGet(`pvp_battle_${_gid}`);
    if (_autoMode >= 2 && _battleId) {
      const _targetMctx = seal.getCtxProxyFirst(ctx, cmdArgs);
      let _targetId = '';
      if (_targetMctx && _targetMctx.player && _targetMctx.player.userId !== ctx.player.userId) {
        _targetId = _targetMctx.player.userId;
      }
      pvpFetch(`/api/pvp/${_battleId}/action`, {
        player_id: ctx.player.userId,
        action: '.s0',
        target: _targetId,
        args: cmdArgs.cleanArgs,
      }).then(_result => {
        if (_result.error) {
          seal.replyToSender(ctx, msg, `[.setab 2] ${_result.message}`);
          return;
        }
        applyServerChanges(_gid, _result);
        let _out = _result.output || '';
        if (_result.needs_reaction && _result.pending_attack) {
          ext.storageSet(`pvp_pending_${_gid}`, JSON.stringify(_result.pending_attack));
        }
        if (_result.auto_turns && _result.auto_turns.length > 0) {
          const nodes = _result.auto_turns.map((t, i) => {
            const label = (_result.turn_labels && _result.turn_labels[i]) || `行动 ${i+1}`;
            return `【${label}】\n${t}`;
          });
          sendForwardMessage(ctx, msg, nodes, 'AI 自动战斗');
        }
        if (_result.state && _result.state.phase !== 'active') {
          ext.storageSet(`pvp_battle_${_gid}`, '');
          _out += '\n\n=== 战斗结束 ===';
        }
        seal.replyToSender(ctx, msg, _out);
      });
      return seal.ext.newCmdExecuteResult(true);
    }

    // ctx = 自身(攻击者), mctx = 对方(目标, 第一个@的人)
    const mctx = seal.getCtxProxyFirst(ctx, cmdArgs);

    if (cmdArgs.at.length === 0) {
      seal.replyToSender(ctx, msg,
        '错误：.bta 必须 @ 一个目标！\n' +
        '格式：.bta <技能> [伤害骰] [贯穿] [致死] @目标');
      return seal.ext.newCmdExecuteResult(true);
    }

    // Parse bonus/penalty suffix from command name
    const cmdName = cmdArgs.command.toLowerCase();
    let bpSuffix = '';
    if (cmdName.startsWith('btab')) bpSuffix = 'b' + (cmdName.substring(4) || '');
    else if (cmdName.startsWith('btap')) bpSuffix = 'p' + (cmdName.substring(4) || '');

    // Parse positional arguments
    let args = cmdArgs.cleanArgs.split(/\s+/).filter(a => a.length > 0);

    // --- Extract n# count from args (any position) ---
    let atkCount = 1;
    args = args.filter(a => {
      const m = a.match(/^(\d+)#$/);
      if (m) { atkCount = Math.max(1, Math.min(20, parseInt(m[1]))); return false; }
      return true;
    });

    let skillName, skillValue, displaySkillName, effectiveSkill;
    let weaponSuffix = '';
    let globalSuffix = '';  // asa/asb global suffix mode
    let damageDice = '';
    let ai = 0;            // index into args for remaining params
    let usedDiceAsSkill = false;

    // --- Global suffix mode: .bta asa 斗殴 ...  → all lookups use suffix 'a' ---
    if (args.length >= 1 && /^as[a-z]+$/i.test(args[0])) {
      globalSuffix = args[0].slice(2).toLowerCase();
      args.shift(); // remove the "asa" token from args
    }

    // --- Determine skill ---
    if (args.length < 1) {
      const best = getBestMeleeSkill(ctx);
      skillName = best.name;
      skillValue = best.value;
      displaySkillName = best.name;
      effectiveSkill = best.value;
    }
    else if (/^\d*d\d+/i.test(args[0]) &&
             (args.length < 2 || !/\d*d\d+/i.test(args[1]))) {
      damageDice = normalizeDice(args[0]);
      const best = getBestMeleeSkill(ctx);
      skillName = best.name;
      skillValue = best.value;
      displaySkillName = best.name;
      effectiveSkill = best.value;
      usedDiceAsSkill = true;
    }
    else {
      skillName = args[0];
      // Per-skill suffix (e.g. "斗殴a") takes precedence over global suffix
      weaponSuffix = getWeaponSuffix(skillName) || globalSuffix;
      const skillParsed = parseSkillValue(ctx, skillName);
      skillValue = skillParsed.value;
      displaySkillName = skillParsed.name;
      if (!skillParsed.isRaw && skillValue <= 0) {
        seal.replyToSender(ctx, msg, `警告：未找到技能【${skillName}】的值，将使用默认值50`);
      }
      effectiveSkill = skillParsed.isRaw ? skillValue : Math.max(skillValue, 1);
    }

    // Parse optional: damage_dice, penetration, lethality
    ai = usedDiceAsSkill ? 1 : (args.length >= 1 ? 1 : 0);
    let penetration = -1;
    let lethality = -1;
    let dmgAdvantage = false;
    let dmgDisadvantage = false;

    if (ai < args.length) {
      let cand = args[ai];
      if (!/^\/[a-c]$/.test(cand)) {
        if (cand.endsWith('优势')) { dmgAdvantage = true; cand = cand.slice(0, -2); }
        else if (cand.endsWith('劣势')) { dmgDisadvantage = true; cand = cand.slice(0, -2); }

        if (/d/i.test(cand)) { damageDice = normalizeDice(cand); ai++; }
        else if (/^\d+$/.test(cand)) {
          // "0" and "1" could be pen/leth if damageDice already has a value
          if ((cand === '0' || cand === '1') && ai+1 < args.length && /^[01]$/.test(args[ai+1])) {
            // Let pen/leth parser consume it below
          } else {
            damageDice = cand; ai++;
          }
        }
        else { damageDice = cand; ai++; }
      }
    }
    if (ai < args.length && /^[01]$/.test(args[ai])) { penetration = parseInt(args[ai]); ai++; }
    if (ai < args.length && /^[01]$/.test(args[ai])) { lethality = parseInt(args[ai]); ai++; }

    // Parse a/b/c sub-target designations (one per @target)
    let subTargets = [];
    while (ai < args.length && /^\/[a-c]$/.test(args[ai])) {
      subTargets.push(args[ai].slice(1)); ai++;
    }
    const firstSubTarget = subTargets.length > 0 ? subTargets[0] : '';

    // Determine effective suffix for attribute lookups (per-skill > global)
    const effSuffix = weaponSuffix || globalSuffix;

    // Fill defaults from attacker's character card (ctx)
    const dmgAttr = effSuffix ? `伤害值${effSuffix}` : '伤害值';
    if (!damageDice || damageDice === '伤害值') {
      let dmgStr = getStrAttr(ctx, dmgAttr);
      if (!dmgStr && effSuffix) dmgStr = getStrAttr(ctx, '伤害值');
      damageDice = dmgStr ? normalizeDice(dmgStr) : '1d4';
    }
    if (!/\d/.test(damageDice)) {
      const lookedUp = getStrAttr(ctx, damageDice);
      if (lookedUp && /\d/.test(lookedUp)) damageDice = normalizeDice(lookedUp);
      else { const rolled = rollDice(ctx, damageDice); damageDice = rolled > 0 ? String(rolled) : '1d4'; }
    }
    damageDice = normalizeDice(damageDice);

    const penAttr = effSuffix ? `伤害贯穿${effSuffix}` : '伤害贯穿';
    if (penetration === -1) {
      const pv = seal.vars.intGet(ctx, penAttr);
      penetration = pv[1] ? (pv[0] ? 1 : 0) : 1;
    }
    const lethAttr = effSuffix ? `致死骰${effSuffix}` : '致死骰';
    if (lethality === -1) {
      const lv = seal.vars.intGet(ctx, lethAttr);
      lethality = lv[1] ? (lv[0] ? 1 : 0) : 1;
    }

    // Roll attack(s) — one D100 per attack
    const attackerName = seal.format(ctx, '{$t玩家}');
    const targetName = seal.format(mctx, '{$t玩家}');
    const groupId = ctx.group ? ctx.group.groupId : 'private';
    initCombatHP(groupId, ctx.player.userId, ctx, mctx.player.userId, mctx);

    // --- Apply active buffs/debuffs to attacker's skill ---
    const gid3 = groupId;
    effectiveSkill = applyBuffSkillMod(gid3, ctx.player.userId, effectiveSkill);

    const atkResults = [];
    for (let n = 0; n < atkCount; n++) {
      // Check buffs for bonus/penalty dice (stacking, max ±3, bonus cancels penalty)
      const buffs = getActiveBuffs(gid3, ctx.player.userId);
      const effectiveBp = calcNetBp(buffs, bpSuffix || '', displaySkillName);
      const rollInfo = rollD100(ctx, effectiveBp);
      const roll = rollInfo.result;
      const rank = successRank(roll, effectiveSkill);
      atkResults.push({ roll, rank, detail: rollInfo.detail });
    }

    // Build combined result line
    let atkResultLine = `${attackerName}的【${displaySkillName}】`;
    if (atkCount > 1) atkResultLine += ` ×${atkCount}`;
    atkResultLine += ` 检定结果为：\n`;
    for (let n = 0; n < atkCount; n++) {
      const ar = atkResults[n];
      const bpD = ar.detail ? `, ${ar.detail}` : '';
      atkResultLine += `  #${n+1}: D100=${ar.roll}/${effectiveSkill}` +
        (bpD ? ` (${ar.roll}[D100=${ar.roll}${bpD}])` : '') +
        ` ${rankText(ctx, ar.rank)}\n`;
    }

    // Ranged: direct damage resolution per attack (no reaction possible)
    if (isRangedSkill(displaySkillName)) {
      let rangedOut = atkResultLine + `\n`;
      rangedOut += `（远程攻击，目标无法闪避/反击，直接结算伤害）\n\n`;
      let totalDmg = 0, hits = 0;
      for (let n = 0; n < atkCount; n++) {
        const ar = atkResults[n];
        if (ar.rank <= 0) { rangedOut += `#${n+1} ${rankText(ctx, ar.rank)}，不命中\n`; continue; }
        hits++;
        const crit = ar.rank >= 4 ? '大成功' : '';
        rangedOut += `#${n+1} 命中 ${rankText(ctx, ar.rank)}${crit ? ' '+crit : ''}\n`;
        const dmgVal = calcDamageAndSyncCard(mctx, ctx, mctx.player.userId, ctx.player.userId, groupId,
          ar.rank, ar.roll, damageDice, penetration, lethality,
          dmgAdvantage, dmgDisadvantage);
        totalDmg += dmgVal;
      }
      if (hits === 0) {
        rangedOut += `\n全部落空！`;
      } else {
        const hpStore = getCombatHP(groupId);
        rangedOut += `\n命中 ${hits}/${atkCount} 次，总伤害 ${totalDmg}`;
        rangedOut += `\n${targetName}剩余HP: ${hpStore[mctx.player.userId]}`;
      }
      seal.replyToSender(ctx, msg, rangedOut);
      return seal.ext.newCmdExecuteResult(true);
    }

    // Melee: store pending attacks for each target (even fumbles — defender may counter)
    const gid = ctx.group ? ctx.group.groupId : 'private';

    // --- Melee range check: attacker & target must be within 5x5 square ---
    if (!isRangedSkill(displaySkillName)) {
      const mapRawChk = ext.storageGet(`combat_map_${gid}`);
      if (mapRawChk) {
        try {
          const mdChk = JSON.parse(mapRawChk);
          if (mdChk.occupants) {
            let atkCoord = '';
            for (const [c, occ] of Object.entries(mdChk.occupants)) {
              if (occ === ctx.player.userId || occ === attackerName) atkCoord = c;
            }
            if (atkCoord) {
              // Check each target
              const outOfRangeTargets = [];
              for (let t = 0; t < cmdArgs.at.length; t++) {
                const tctxCheck = t === 0 ? mctx : seal.getCtxProxyAtPos(ctx, cmdArgs, t);
                if (!tctxCheck || !tctxCheck.player) continue;
                const tNameCheck = seal.format(tctxCheck, '{$t玩家}');
                let tgtCoord = '';
                for (const [c, occ] of Object.entries(mdChk.occupants)) {
                  if (occ === tctxCheck.player.userId || occ === tNameCheck) { tgtCoord = c; break; }
                }
                if (tgtCoord && !isInMeleeRange(atkCoord, tgtCoord)) {
                  outOfRangeTargets.push(`${tNameCheck}(${tgtCoord})`);
                }
              }
              if (outOfRangeTargets.length > 0) {
                seal.replyToSender(ctx, msg,
                  `近战攻击无法触及以下目标（近战范围：以自身为中心的5×5正方形）：\n` +
                  `你的位置: ${atkCoord}\n` +
                  outOfRangeTargets.map(t => `  ${t} — 超出范围`).join('\n'));
                return seal.ext.newCmdExecuteResult(true);
              }
            }
          }
        } catch(e) {}
      }
    }

    const restimV = seal.vars.intGet(ctx, '$gRestimMode');
    const attackerRestimMode = restimV[1] ? restimV[0] : 0;
    const basePending = {
      attackerName, attackerUserId: ctx.player.userId,
      skillName: displaySkillName, skillValue: effectiveSkill,
      atkResults, atkCount,
      damageDice, penetration, lethality,
      bpSuffix, dmgAdvantage, dmgDisadvantage,
      groupId: gid, atkResultLine,
      attackerRestimMode: attackerRestimMode,
      ts: Date.now()
    };

    // Check NPC preset reactions
    const reKey = `bta_npc_reaction_${gid}`;
    let npcReactions = {};
    const reRaw = ext.storageGet(reKey);
    if (reRaw) { try { npcReactions = JSON.parse(reRaw); } catch(e) {} }

    // Build target list: iterate all @mentioned users from cmdArgs.at
    const targets = [];
    let autoResolveOut = '';
    for (let t = 0; t < cmdArgs.at.length; t++) {
      const tctx = t === 0 ? mctx : seal.getCtxProxyAtPos(ctx, cmdArgs, t);
      if (!tctx || !tctx.player) continue;
      const tName = seal.format(tctx, '{$t玩家}');
      const tSub = t < subTargets.length ? subTargets[t] : '';
      const tid = tctx.player.userId;
      // Per-target pending key
      const pKey = `pending_atk_${gid}_${tid}`;
      initCombatHP(gid, ctx.player.userId, ctx, tid, tctx);
      const pendingData = Object.assign({}, basePending, {
        targetName: tName, targetUserId: tid, subTarget: tSub
      });
      ext.storageSet(pKey, JSON.stringify(pendingData));

      // Check if NPC has preset reaction → auto-resolve
      const npcReact = npcReactions[tName] || npcReactions[tid];
      if (npcReact) {
        let autoResult = '';
        // Simple auto-resolution: roll NPC's dodge/best melee
        if (npcReact === 'd') {
          const dodgeVal = getAttr(tctx, '闪避', 25);
          const reactRoll = rollD100(tctx, '');
          const reactRank = successRank(reactRoll.result, dodgeVal);
          autoResult = `  ${tName} 自动闪避: D100=${reactRoll.result}/${dodgeVal} ${rankText(ctx, reactRank)}\n`;
          // Simulate dodge effect on attack ranks
          let anyHit = false;
          for (const ar of atkResults) {
            let effAtk = ar.rank;
            if (reactRank > 1) effAtk -= (reactRank - 1);
            if (reactRank === -2) effAtk += (effAtk === -1) ? 2 : 1;
            effAtk = Math.max(-2, Math.min(4, effAtk));
            if (effAtk > 0) {
              anyHit = true;
              calcDamageAndSyncCard(tctx, ctx, tid, ctx.player.userId, gid,
                effAtk, ar.roll, damageDice, penetration, lethality, dmgAdvantage, dmgDisadvantage);
            }
          }
          if (anyHit) {
            const hpStore = getCombatHP(gid);
            autoResult += `  命中！剩余HP: ${hpStore[tid]}\n`;
          } else {
            autoResult += '  闪避成功！\n';
          }
        } else if (npcReact === 'c') {
          const best = getBestMeleeSkill(tctx);
          const reactRoll = rollD100(tctx, '');
          const reactRank = successRank(reactRoll.result, best.value);
          autoResult = `  ${tName} 自动反击【${best.name}=${best.value}】: D100=${reactRoll.result}/${best.value} ${rankText(ctx, reactRank)}\n`;
          // Simulate counter resolution
          let anyCounterHit = false;
          for (const ar of atkResults) {
            let effAtk = ar.rank, effReact = reactRank;
            if (reactRank === -2) effAtk += (effAtk === -1) ? 2 : 1;
            if (effAtk === -2) effReact = Math.min(4, effReact + 1);
            effAtk = Math.max(-2, Math.min(4, effAtk));
            if (effReact > effAtk && effReact > 0) {
              anyCounterHit = true;
              calcDamageValue(ctx, ctx.player.userId, tid, gid,
                effReact, reactRoll.result, '1d4', 1, 0, false, false);
            } else if (effAtk > effReact && effAtk > 0) {
              calcDamageAndSyncCard(tctx, ctx, tid, ctx.player.userId, gid,
                effAtk, ar.roll, damageDice, penetration, lethality, dmgAdvantage, dmgDisadvantage);
            }
          }
          if (anyCounterHit) {
            const hpStore = getCombatHP(gid);
            autoResult += `  反击命中！攻方剩余HP: ${hpStore[ctx.player.userId]}\n`;
          } else {
            autoResult += '  反击未生效\n';
          }
        }
        // Clear pending for auto-resolved NPC
        ext.storageSet(pKey, '');
        autoResolveOut += autoResult;
      } else {
        targets.push({ name: tName, subTarget: tSub, userId: tid });
      }
    }
    // Store list of pending target userIds for cleanup
    ext.storageSet(`pending_targets_${gid}`, JSON.stringify(targets.map(t => t.userId)));

    // Output based on restim mode
    const restimMode = attackerRestimMode;
    const hasFumble = atkResults.some(a => a.rank === -2);
    const fumbleNote = hasFumble ? '（攻击方有大失败！反击等级+1）\n' : '';

    let reply = '';
    if (restimMode === 0) {
      reply += atkResultLine + '\n';
    }
    // Show auto-resolved NPC results
    if (autoResolveOut) {
      reply += '\n' + autoResolveOut;
    }
    // If no player targets remain, just output
    if (targets.length === 0) {
      reply += '\n（全部目标已自动结算）';
      seal.replyToSender(ctx, msg, reply);
      return seal.ext.newCmdExecuteResult(true);
    }

    if (restimMode === 1) {
      // Delayed: just prompt each target
      const targetDescs = targets.map(t =>
        `@${t.name}${t.subTarget ? '/' + t.subTarget : ''}`).join(' ');
      reply = `${targetDescs} 请做出反应：\n` + fumbleNote +
        `.e 闪避 / .e d\n` +
        `.e 反击 [技能名=最高近战] [伤害骰] [贯穿gc/ngc] [致死] / .e c [...]`;
    } else {
      // Immediate: show attack roll then prompt each target
      reply = atkResultLine + `\n` + fumbleNote;
      for (const t of targets) {
        const subLabel = t.subTarget ? `${t.subTarget}的` : '';
        reply += `@${t.name} 请输入${subLabel}反应：\n`;
      }
      reply += `.e 闪避 / .e d\n` +
        `.e 反击 [技能名=最高近战] [伤害骰] [贯穿gc/ngc] [致死] / .e c [...]`;
    }
    seal.replyToSender(ctx, msg, reply);
    return seal.ext.newCmdExecuteResult(true);
  };
  return cmd;
}

// ============================================================
//  .e / .eb / .ep  — 反应指令（闪避 / 反击）
//  Syntax: .e 闪避   OR   .e 反击 [skill] [dmg] [贯穿/非贯穿/gc/ngc] [致死]
//  Suffix: .e 闪避a / .e 反击a 斗殴a ...  (use character a's skills)
// ============================================================
function makeECmd(baseName) {
  const cmd = seal.ext.newCmdItemInfo();
  cmd.name = baseName;
  cmd.help =
    '.e 闪避 / .e d  // 闪避（成功等级稀释攻击方）\n' +
    '.e 闪避a / .e d a  // 使用后缀a的技能值进行闪避\n' +
    '.e 反击 [技能=最高近战技能] [伤害骰] [贯穿gc/ngc] [致死]  /  .e c [...]  // 反击\n' +
    '.e c 技能a ...  // 反击使用后缀a的技能和伤害值\n' +
    '.eb / .ep // 奖惩骰变体';
  cmd.allowDelegate = true;
  cmd.solve = (ctx, msg, cmdArgs) => {
    // ctx = 自身(反应者)
    const defCtx = ctx;
    let gid = ctx.group ? ctx.group.groupId : 'private';

    // --- .setab 2 HTTP path: route reaction through Python backend ---
    const _autoMode = getAutoMode(ctx);
    const _battleId = ext.storageGet(`pvp_battle_${gid}`);
    if (_autoMode >= 2 && _battleId) {
      const pendingRaw = ext.storageGet(`pvp_pending_${gid}`);
      if (!pendingRaw) {
        seal.replyToSender(ctx, msg, '[.setab 2] 当前没有待处理的反应！');
        return seal.ext.newCmdExecuteResult(true);
      }
      let pending;
      try { pending = JSON.parse(pendingRaw); } catch (e) {
        seal.replyToSender(ctx, msg, '[.setab 2] 待处理反应数据损坏，无法做出反应。');
        ext.storageSet(`pvp_pending_${gid}`, '');
        return seal.ext.newCmdExecuteResult(true);
      }
      if (pending.defender_id && pending.defender_id !== ctx.player.userId) {
        seal.replyToSender(ctx, msg, `[.setab 2] 该反应的目标是 ${pending.defender_name}，不是你！`);
        return seal.ext.newCmdExecuteResult(true);
      }
      // Parse reaction type
      const args = cmdArgs.cleanArgs.split(/\s+/).filter(a => a.length > 0);
      if (args.length < 1) {
        seal.replyToSender(ctx, msg, '[.setab 2] 请指定反应类型：\n.e 闪避 / .e d\n.e 反击 / .e c');
        return seal.ext.newCmdExecuteResult(true);
      }
      const reactType = args[0];
      const isDodge = reactType === '闪避' || reactType === 'd' || reactType === '闪' || reactType === 'dodge';
      const isCounter = reactType === '反击' || reactType === 'c' || reactType === 'counter' || reactType === '反';
      if (!isDodge && !isCounter) {
        seal.replyToSender(ctx, msg, '[.setab 2] 不支持的反应类型。请使用：闪避/d 或 反击/c');
        return seal.ext.newCmdExecuteResult(true);
      }
      const choice = isDodge ? 'dodge' : 'counter';
      pvpFetch(`/api/pvp/${_battleId}/react`, {
        player_id: ctx.player.userId,
        choice: choice,
      }).then(_result => {
        if (_result.error) {
          seal.replyToSender(ctx, msg, `[.setab 2] ${_result.message}`);
          return;
        }
        ext.storageSet(`pvp_pending_${gid}`, '');
        applyServerChanges(gid, _result);
        let _out = _result.output || '';
        // Check for AI turns after reaction
        if (_result.auto_turns && _result.auto_turns.length > 0) {
          const nodes = _result.auto_turns.map((t, i) => {
            const label = (_result.turn_labels && _result.turn_labels[i]) || `行动 ${i+1}`;
            return `【${label}】\n${t}`;
          });
          sendForwardMessage(ctx, msg, nodes, 'AI 自动战斗');
        }
        // Check if AI turns triggered another reaction
        if (_result.needs_reaction && _result.pending_attack) {
          ext.storageSet(`pvp_pending_${gid}`, JSON.stringify(_result.pending_attack));
        }
        if (_result.state && _result.state.phase !== 'active') {
          ext.storageSet(`pvp_battle_${gid}`, '');
          _out += '\n\n=== 战斗结束 ===';
        }
        seal.replyToSender(ctx, msg, _out);
      });
      return seal.ext.newCmdExecuteResult(true);
    }

    // Load pending attack — keyed by group + target user (multi-target support)
    let pendingKey = `pending_atk_${gid}_${ctx.player.userId}`;
    let raw = ext.storageGet(pendingKey);
    let pending = null;
    // Fallback: try old-style key for backward compat
    if (!raw) {
      const oldKey = `pending_atk_${gid}`;
      const oldRaw = ext.storageGet(oldKey);
      if (oldRaw) {
        try { pending = JSON.parse(oldRaw); } catch (e) {}
        ext.storageSet(oldKey, ''); // clear old key
        if (pending && pending.targetUserId && ctx.player.userId !== pending.targetUserId) {
          seal.replyToSender(ctx, msg, `该攻击的目标是 ${pending.targetName}，不是你！`);
          return seal.ext.newCmdExecuteResult(true);
        }
      }
    }
    // If not found, also try spell pending (法术伤害可反应)
    if (!raw && !pending) {
      const spellKey = `pending_spell_${gid}_${ctx.player.userId}`;
      const spellRaw = ext.storageGet(spellKey);
      if (spellRaw) {
        raw = spellRaw; pendingKey = spellKey;
      }
    }
    if (!raw && !pending) {
      seal.replyToSender(ctx, msg, '当前没有待处理的攻击！请先由攻击方使用 .bta 发起攻击。');
      return seal.ext.newCmdExecuteResult(true);
    }
    if (raw) {
      try { pending = JSON.parse(raw); } catch (e) {
        seal.replyToSender(ctx, msg, '待处理攻击数据损坏，请重新 .bta。');
        ext.storageSet(pendingKey, '');
        return seal.ext.newCmdExecuteResult(true);
      }
    }

    // Identity check: only the targeted player can respond
    if (pending && pending.targetUserId && ctx.player.userId !== pending.targetUserId) {
      seal.replyToSender(ctx, msg, `该攻击的目标是 ${pending.targetName}，不是你！`);
      return seal.ext.newCmdExecuteResult(true);
    }

    // Parse bonus/penalty
    const cmdName = cmdArgs.command.toLowerCase();
    let bpSuffix = '';
    if (cmdName.startsWith('eb')) bpSuffix = 'b' + (cmdName.substring(2) || '');
    else if (cmdName.startsWith('ep')) bpSuffix = 'p' + (cmdName.substring(2) || '');

    // Parse reaction type
    const args = cmdArgs.cleanArgs.split(/\s+/).filter(a => a.length > 0);
    if (args.length < 1) {
      seal.replyToSender(ctx, msg, '请指定反应类型：\n.e 闪避 或 .e d\n.e 反击 [技能名] 或 .e c [技能名]');
      return seal.ext.newCmdExecuteResult(true);
    }

    const reactType = args[0];
    const isDodge = reactType === '闪避' || reactType === 'd' || reactType === '闪' || reactType === 'dodge';
    const isCounter = reactType === '反击' || reactType === 'c' || reactType === 'counter' || reactType === '反';
    // .e s<序号> — 反应技能（法术时机=4）
    const skillMatch = /^s(\d+)$/i.test(reactType);
    const isReactionSkill = skillMatch;

    if (!isDodge && !isCounter && !isReactionSkill) {
      seal.replyToSender(ctx, msg, '不支持的反应类型。请使用：闪避/d、反击/c、或 .e s<序号>（反应技能）');
      return seal.ext.newCmdExecuteResult(true);
    }

    // Check 可反应 attribute on defender's card
    const canReact = getAttr(defCtx, '可反应', 1);
    if (!canReact) {
      seal.replyToSender(ctx, msg, '你的角色卡设置了不可反应（可反应=0），无法执行闪避/反击！');
      return seal.ext.newCmdExecuteResult(true);
    }

    // --- Suffix detection for .e ---
    // .e 闪避a  /  .e d a  /  .e 反击a  /  .e c 技能a
    let reactSuffix = '';
    if (/[ab]$/.test(reactType) && reactType.length > 1) {
      reactSuffix = reactType.slice(-1);
    }
    let argIdx = 1;
    // Check if next arg is a single suffix letter
    if (!reactSuffix && argIdx < args.length && /^[a-c]$/.test(args[argIdx])) {
      reactSuffix = args[argIdx]; argIdx++;
    }

    // Gather reaction parameters
    let reactSkillName, reactSkillValue;
    let reactDmgDice = '';
    let reactPen = pending.penetration;
    let reactLeth = pending.lethality;

    if (isDodge) {
      reactSkillName = '闪避' + reactSuffix;
      reactSkillValue = getAttr(defCtx, reactSkillName);
      if (reactSkillValue <= 0 && reactSuffix) {
        // Fallback to unsuffixed 闪避
        reactSkillValue = getAttr(defCtx, '闪避');
      }
      if (reactSkillValue <= 0) {
        const dex = getAttr(defCtx, '敏捷', 50);
        reactSkillValue = Math.floor(dex / 2);
        if (reactSkillValue <= 0) reactSkillValue = 25;
      }
    } else if (isCounter) {
      // Counter-attack: default to highest-value melee combat skill
      // Apply suffix to default skill lookup
      let best = getBestMeleeSkill(defCtx);
      if (reactSuffix) {
        // Try suffixed version of best skill first
        const suffixedName = best.name + reactSuffix;
        const sv = getAttr(defCtx, suffixedName);
        if (sv > 0) { best = { name: suffixedName, value: sv }; }
      }
      reactSkillName = best.name;
      reactSkillValue = best.value;

      if (argIdx < args.length) {
        const cand = args[argIdx];
        // Skip pen/leth keywords (pure numbers handled below)
        const isPenKW = (cand === '贯穿' || cand === '非贯穿' || cand === 'gc' || cand === 'ngc');

        // Pure number → raw skill value (e.g. ".e c 70")
        if (/^\d+$/.test(cand)) {
          reactSkillName = cand;
          reactSkillValue = parseInt(cand);
          argIdx++;
        }
        // Contains 'd' → damage dice (e.g. ".e c 3d6"), keep best melee as skill
        else if (/d/i.test(cand)) {
          // skill stays as best melee; damage parsed below
        }
        // Non-numeric word that isn't a pen keyword → skill name
        else if (!isPenKW) {
          argIdx++;
          // Use parseSkillValue to handle skill+N / skill-N / plain skill
          const p = parseSkillValue(defCtx, cand);
          reactSkillName = p.name + reactSuffix;
          reactSkillValue = Math.max(p.value, p.isRaw ? p.value : 50);
          // If suffixed version exists on card, use it
          if (reactSuffix) {
            const sv2 = getAttr(defCtx, reactSkillName);
            if (sv2 > 0) reactSkillValue = sv2;
          }
        }
      }

      // Damage dice for counter — only dice expressions (containing 'd')
      if (argIdx < args.length && /d/i.test(args[argIdx])) {
        reactDmgDice = normalizeDice(args[argIdx]); argIdx++;
      }
      // Default: defender's own 伤害值 (with suffix if applicable)
      if (!reactDmgDice) {
        reactDmgDice = reactSuffix ? getStrAttr(defCtx, `伤害值${reactSuffix}`) : '';
        if (!reactDmgDice && reactSuffix) reactDmgDice = getStrAttr(defCtx, '伤害值');
      }
      if (!reactDmgDice) reactDmgDice = pending.damageDice;
      reactDmgDice = normalizeDice(reactDmgDice);

      // Penetration
      if (argIdx < args.length) {
        const pa = args[argIdx];
        if (pa === '贯穿' || pa === 'gc') { reactPen = 1; argIdx++; }
        else if (pa === '非贯穿' || pa === 'ngc') { reactPen = 0; argIdx++; }
        else if (pa === '0') { reactPen = 0; argIdx++; }
        else if (pa === '1') { reactPen = 1; argIdx++; }
      }
      if (argIdx < args.length && /^[01]$/.test(args[argIdx])) { reactLeth = parseInt(args[argIdx]); }
    } else if (isReactionSkill) {
      // Reaction skill (.e s<序号>) — lookup spell with timing=4
      const skillNum = parseInt(reactType.slice(1));
      const spells = getCachedSpells(defCtx, gid);
      const spell = spells.find(s => s.index === skillNum);
      if (!spell) {
        seal.replyToSender(ctx, msg, `未找到技能${skillNum}！`);
        return seal.ext.newCmdExecuteResult(true);
      }
      const timing = spell['时机'] || 2;
      if (!hasTiming(timing, 4)) {  // 4 = 反应
        seal.replyToSender(ctx, msg, `【${spell.name}】不能作为反应动作使用（时机: ${TIMING_NAMES[timing]||timing}）`);
        return seal.ext.newCmdExecuteResult(true);
      }
      reactSkillName = spell.name;
      reactSkillValue = 99;  // Reaction skills don't need a roll value, use spell logic
    }

    // --- Multi-attack resolution: each attack gets its own reaction roll ---
    const atkResults = pending.atkResults || [{ roll: pending.attackRoll, rank: pending.attackRank, detail: pending.bpDetail || '' }];
    const atkCount = pending.atkCount || 1;
    gid = pending.groupId || gid;  // prefer pending's groupId, fallback to already-declared gid
    const attackerName = pending.attackerName;
    const defenderName = seal.format(defCtx, '{$t玩家}');

    // Check if this is a spell pending → only dodge allowed
    const isSpellPending = pending.type === 'spell';
    if (isSpellPending && isCounter) {
      seal.replyToSender(ctx, msg, '法术攻击只能以闪避反应，不能反击！请使用 .e 闪避 / .e d');
      return seal.ext.newCmdExecuteResult(true);
    }

    let out = '';
    const attackerRestim = pending.attackerRestimMode || 0;
    if (attackerRestim === 1) {
      out += pending.atkResultLine + '\n\n';
    }

    const reactLabel = isDodge ? '闪避' : `反击:${reactSkillName}`;
    out += `对手 ${defenderName}的反应：【${reactLabel}】×${atkCount}：\n`;

    // Roll one reaction per attack, resolve independently
    let atkHits = 0, atkTotalDmg = 0, defHits = 0, defTotalDmg = 0;
    for (let n = 0; n < atkCount; n++) {
      const ar = atkResults[n];

      // Roll reaction for this attack — apply buffs to defender's skill
      const reactGid = pending.groupId || gid;
      reactSkillValue = applyBuffSkillMod(reactGid, ctx.player.userId, reactSkillValue);
      // Check buffs for bonus/penalty dice (stacking, max ±3) on defender
      const reactBuffs = getActiveBuffs(reactGid, ctx.player.userId);
      const reactEffectiveBp = calcNetBp(reactBuffs, bpSuffix || '', reactSkillName);
      const reactRollInfo = rollD100(defCtx, reactEffectiveBp);
      const reactRoll = reactRollInfo.result;
      const rawReactRank = successRank(reactRoll, reactSkillValue);
      const reactBpDetail = reactRollInfo.detail ? `, ${reactRollInfo.detail}` : '';

      let effAtk = ar.rank;
      let effReact = rawReactRank;

      // Reaction modifiers
      if (isDodge) {
        if (rawReactRank > 1) effAtk -= (rawReactRank - 1);
        if (rawReactRank === -2) effAtk += (effAtk === -1) ? 2 : 1;
      }
      if (isCounter) {
        if (rawReactRank === -2) effAtk += (effAtk === -1) ? 2 : 1;
        if (effAtk === -2) effReact = Math.min(4, effReact + 1);
      }
      effAtk = Math.max(-2, Math.min(4, effAtk));

      // Determine winner for this attack
      const atkPass = effAtk > 0;
      const defPass = effReact > 0;
      let winner = 0;
      if (atkPass && defPass) {
        if (effAtk > effReact) winner = 1;
        else if (effAtk < effReact) winner = 2;
        else winner = isDodge ? 2 : 1;
      } else if (atkPass && !defPass) winner = 1;
      else if (!atkPass && defPass) winner = 2;

      out += `  #${n+1}: 攻${rankText(ctx, ar.rank)} vs 防D100=${reactRoll}/${reactSkillValue}` +
        (reactBpDetail ? ` (${reactRoll}[D100=${reactRoll}${reactBpDetail}])` : '') +
        ` ${rankText(ctx, rawReactRank)} → `;

      if (winner === 1) {
        atkHits++;
        const dmg = calcDamageAndSyncCard(ctx, ctx, pending.targetUserId, pending.attackerUserId, gid,
          effAtk, ar.roll, pending.damageDice, pending.penetration, pending.lethality,
          pending.dmgAdvantage, pending.dmgDisadvantage);
        atkTotalDmg += dmg;
        out += `攻方命中！(+${dmg})\n`;
      } else if (winner === 2 && isCounter) {
        defHits++;
        const dmg = calcDamageValue(ctx, pending.attackerUserId, pending.targetUserId, gid,
          effReact, reactRoll, reactDmgDice, reactPen, reactLeth,
          false, false);
        defTotalDmg += dmg;
        out += `反击命中！(+${dmg})\n`;
      } else if (winner === 2 && isDodge) {
        out += `闪避成功\n`;
      } else {
        out += `未通过\n`;
      }
    }

    // Summary
    out += `\n`;
    if (atkHits > 0) {
      out += `${attackerName} 命中 ${atkHits}/${atkCount} 次，总伤害 ${atkTotalDmg}`;
      const hpStore = getCombatHP(gid);
      if (hpStore[pending.targetUserId] !== undefined) {
        out += `\n${pending.targetName}剩余HP: ${hpStore[pending.targetUserId]}`;
      }
    }
    if (defHits > 0) {
      if (atkHits > 0) out += `\n`;
      out += `${defenderName} 反击命中 ${defHits}/${atkCount} 次，总伤害 ${defTotalDmg}`;
      const hpStore = getCombatHP(gid);
      if (hpStore[pending.attackerUserId] !== undefined) {
        out += `\n${attackerName}剩余HP: ${hpStore[pending.attackerUserId]}`;
      }
    }
    if (atkHits === 0 && defHits === 0) {
      out += `无人得手！`;
    }

    ext.storageSet(pendingKey, '');
    // Remove this target from tracking list
    const tgtKey = `pending_targets_${gid}`;
    const tgtRaw = ext.storageGet(tgtKey);
    if (tgtRaw) {
      try {
        let tids = JSON.parse(tgtRaw);
        tids = tids.filter(tid => tid !== ctx.player.userId);
        ext.storageSet(tgtKey, tids.length > 0 ? JSON.stringify(tids) : '');
      } catch (e) {}
    }
    seal.replyToSender(ctx, msg, out);
    return seal.ext.newCmdExecuteResult(true);
  };
  return cmd;
}

// ============================================================
//  .hs  — 魔法少女变身
//  变身后所有战斗类技能（近战+射击+投掷+爆破+炮术）只要
//  人物卡属性表中存在，均 +20。
// ============================================================
const cmdHs = seal.ext.newCmdItemInfo();
cmdHs.name = 'hs';
cmdHs.help =
  '.hs // 魔法少女变身（自动 .btastart）\n' +
  'HP ×(5+level)  MP ×(2+0.5×level)  APP+60(上限80)\n' +
  '全部战斗类技能（只要属性表有值）+20  体格+1  行动力+2\n' +
  '变身同时自动开启战斗同步（等同于 .btastart）';
cmdHs.allowDelegate = true;
cmdHs.solve = (ctx, msg, cmdArgs) => {
  const mctx = seal.getCtxProxyFirst(ctx, cmdArgs);

  const alreadyV = seal.vars.intGet(mctx, '$gHsTransformed');
  if (alreadyV[1] && alreadyV[0]) {
    seal.replyToSender(ctx, msg, '已经处于变身状态！请先 .unh 解除变身。');
    return seal.ext.newCmdExecuteResult(true);
  }

  const lvV = seal.vars.intGet(mctx, '等级');
  const level = (lvV[1] && lvV[0]) ? lvV[0] : 1;

  const origHP  = getAttr(mctx, '体力上限') || getAttr(mctx, 'hp') || 10;
  const origMP  = getAttr(mctx, '魔力上限') || getAttr(mctx, '魔力') || getAttr(mctx, 'mp') || 10;
  const origAPP = getAttr(mctx, '外貌') || getAttr(mctx, 'app') || 50;
  const origDodge    = getAttr(mctx, '闪避', 25);
  const origBuild = getAttr(mctx, '体格', 0);
  const origMOV   = getAttr(mctx, '行动力') || getAttr(mctx, 'mov', 8);
  const origSAN   = getAttr(mctx, '理智') || getAttr(mctx, 'san', 50);
  const origCON   = getAttr(mctx, '体质') || getAttr(mctx, 'con', 50);
  const origSIZ   = getAttr(mctx, '体型') || getAttr(mctx, 'siz', 50);
  const origCM    = getAttr(mctx, 'cm') || getAttr(mctx, '克苏鲁神话', 0);

  const curHP = getAttr(mctx, '体力', origHP);
  const curMP = getAttr(mctx, '魔力', origMP);

  // Collect original values for ALL combat skills that exist on the character card
  const origCombatSkills = {};
  const boostedSkills = [];   // for display
  for (const s of ALL_COMBAT_SKILLS) {
    const v = seal.vars.intGet(mctx, s);
    if (v[1]) {
      origCombatSkills[s] = v[0];
      const newVal = v[0] + 20;
      if (newVal !== v[0]) boostedSkills.push({ name: s, orig: v[0], now: newVal });
    }
  }

  // Also save orig dodge separately (not in ALL_COMBAT_SKILLS)
  // 闪避 is handled separately

  const key = `hs_orig_${mctx.player.userId}`;
  ext.storageSet(key, JSON.stringify({
    hpMax: origHP, mpMax: origMP, app: origAPP,
    dodge: origDodge,
    build: origBuild, mov: origMOV, san: origSAN, con: origCON,
    siz: origSIZ, cm: origCM,
    curHP: curHP, curMP: curMP, level: level,
    combatSkills: origCombatSkills
  }));

  const newHPmax = Math.floor(origHP * (5 + level));
  const newMPmax = Math.floor(origMP * (2 + 0.5 * level));
  const newAPP = Math.min(origAPP + 60, 80);
  const newDodge = Math.min(origDodge + 20, 80);
  const newBuild = origBuild + 1;
  const newMOV   = origMOV + 2;
  const newDB = dbFromBuild(newBuild);

  seal.vars.intSet(mctx, '体力上限', newHPmax);
  seal.vars.intSet(mctx, '体力', newHPmax);
  seal.vars.intSet(mctx, '魔力上限', newMPmax);
  seal.vars.intSet(mctx, '魔力', newMPmax);
  seal.vars.intSet(mctx, '外貌', newAPP);
  seal.vars.intSet(mctx, '闪避', newDodge);
  seal.vars.intSet(mctx, '体格', newBuild);
  seal.vars.intSet(mctx, '行动力', newMOV);
  seal.vars.intSet(mctx, '$gHsTransformed', 1);
  seal.vars.intSet(mctx, '$gHsSpellCount', 0);
  seal.vars.intSet(mctx, '$gHsCombatFlag', 0);

  // Apply +20 (max 80) to ALL combat skills that exist in attr table
  for (const b of boostedSkills) {
    seal.vars.intSet(mctx, b.name, b.now);
  }

  // Auto .btastart: mark combat active, init combat HP
  seal.vars.intSet(mctx, '$gCombatActive', 1);
  const hsGid = mctx.group ? mctx.group.groupId : 'private';
  initCombatHP(hsGid, mctx.player.userId, mctx, mctx.player.userId, mctx);

  const pn = seal.format(mctx, '{$t玩家}');
  let out = `${pn} 进行了魔法少女变身！\n\n`;
  out += `HP : ${origHP} → ${newHPmax}  (×${(5+level)})\n`;
  out += `MP : ${origMP} → ${newMPmax}  (×${(2+0.5*level).toFixed(1)})\n`;
  out += `APP: ${origAPP} → ${newAPP}  (+60, max80)\n`;
  out += `闪避: ${origDodge} → ${newDodge}  (+20, max80)\n`;
  out += `体格: ${origBuild} → ${newBuild}  (+1, DB→${newDB})\n`;
  out += `行动力: ${origMOV} → ${newMOV}  (+2)\n`;
  out += `\n（战斗同步已自动开启，.st 修改属性将实时生效）\n`;
  if (boostedSkills.length > 0) {
    out += `\n【战斗技能】（+20）:\n`;
    // Split into lines: ~6 skills per line for readability
    const names = boostedSkills.map(b => `${b.name}: ${b.orig}→${b.now}`);
    for (let i = 0; i < names.length; i += 4) {
      out += names.slice(i, i + 4).join('  ') + '\n';
    }
  }
  seal.replyToSender(ctx, msg, out);
  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  .unh  — 解除变身（.setab 0 模式：不自动结算成长）
// ============================================================
const cmdUnh = seal.ext.newCmdItemInfo();
cmdUnh.name = 'unh';
cmdUnh.help =
  '.unh // 解除魔法少女变身\n' +
  '恢复原始属性。根据SAN等级触发不同SC与CON检定。\n' +
  '（.setab 0 模式下不自动结算升级）';
cmdUnh.allowDelegate = true;
cmdUnh.solve = (ctx, msg, cmdArgs) => {
  const mctx = seal.getCtxProxyFirst(ctx, cmdArgs);

  const alreadyV = seal.vars.intGet(mctx, '$gHsTransformed');
  if (!alreadyV[1] || !alreadyV[0]) {
    seal.replyToSender(ctx, msg, '当前未处于变身状态！');
    return seal.ext.newCmdExecuteResult(true);
  }

  const key = `hs_orig_${mctx.player.userId}`;
  const raw = ext.storageGet(key);
  if (!raw) {
    seal.replyToSender(ctx, msg, '未找到变身前的数据记录，将仅清除变身标记。');
    seal.vars.intSet(mctx, '$gHsTransformed', 0);
    return seal.ext.newCmdExecuteResult(true);
  }

  let orig;
  try { orig = JSON.parse(raw); } catch (e) {
    seal.replyToSender(ctx, msg, '变身数据损坏。');
    seal.vars.intSet(mctx, '$gHsTransformed', 0);
    return seal.ext.newCmdExecuteResult(true);
  }

  const curMPV = seal.vars.intGet(mctx, '魔力');
  const curMP = curMPV[1] ? curMPV[0] : orig.mpMax;

  // Prefer combat HP (战斗内HP) over card HP for post-transform calculation
  const gid = mctx.group ? mctx.group.groupId : 'private';
  const combatHP = getCombatHP(gid);
  const combatCurHP = combatHP[mctx.player.userId];
  const cardHPV = seal.vars.intGet(mctx, '体力');
  let curHP;
  if (combatCurHP !== undefined) {
    curHP = combatCurHP;
  } else {
    curHP = cardHPV[1] ? cardHPV[0] : orig.hpMax;
  }

  const restoredHP = Math.min(Math.ceil(curHP / (orig.level + 5)) + 2, orig.hpMax);
  const restoredMP = Math.min(curMP, orig.mpMax);

  // SC & CON check with full SAN tiers
  const spellCountV = seal.vars.intGet(mctx, '$gHsSpellCount');
  const combatFlagV = seal.vars.intGet(mctx, '$gHsCombatFlag');
  const spellCount = spellCountV[1] ? spellCountV[0] : 0;
  const combatFlag = combatFlagV[1] ? combatFlagV[0] : 0;
  let scOut = '';

  if ((spellCount > 2) || (combatFlag > 0)) {
    const curSAN = getAttr(mctx, '理智') || orig.san || 50;
    const curCON = getAttr(mctx, '体质') || orig.con || 50;
    const curCM  = getAttr(mctx, 'cm') || getAttr(mctx, '克苏鲁神话') || orig.cm || 0;

    let scSuccessExpr, scFailExpr, conFailExpr, conSuccessExpr;
    let halveLosses = false;

    if (curSAN < (curCM || 0) && curCM > 0) halveLosses = true;

    if (curSAN < 20) {
      scSuccessExpr = '1d3'; scFailExpr = '1d8';
      conFailExpr = '1d6'; conSuccessExpr = '1d3';
    } else if (curSAN < 30) {
      scSuccessExpr = '1'; scFailExpr = '1d6';
      conFailExpr = '1d3+1'; conSuccessExpr = '0';
    } else {
      scSuccessExpr = '0'; scFailExpr = '1d5';
      conFailExpr = '1d3'; conSuccessExpr = '0';
    }

    const sanCheck = rollDice(mctx, '1d100');
    if (sanCheck > curSAN) {
      let sanLoss = rollDice(mctx, scFailExpr);
      if (halveLosses) sanLoss = Math.floor(sanLoss / 2);
      seal.vars.intSet(mctx, '理智', Math.max(0, curSAN - sanLoss));
      scOut += `\nSC检定：D100=${sanCheck}/${curSAN} 失败，扣除理智 ${sanLoss} 点（${curSAN}→${Math.max(0, curSAN - sanLoss)}）。`;
    } else {
      let sanLoss = rollDice(mctx, scSuccessExpr);
      if (halveLosses) sanLoss = Math.floor(sanLoss / 2);
      if (sanLoss > 0) {
        seal.vars.intSet(mctx, '理智', Math.max(0, curSAN - sanLoss));
        scOut += `\nSC检定：D100=${sanCheck}/${curSAN} 成功，仍扣除理智 ${sanLoss} 点（${curSAN}→${Math.max(0, curSAN - sanLoss)}）。`;
      } else {
        scOut += `\nSC检定：D100=${sanCheck}/${curSAN} 成功，无需扣除理智。`;
      }
    }

    const conCheck = rollDice(mctx, '1d100');
    if (conCheck > curCON) {
      let conLoss = rollDice(mctx, conFailExpr);
      if (halveLosses) conLoss = Math.floor(conLoss / 2);
      const newCON = Math.max(0, curCON - conLoss);
      seal.vars.intSet(mctx, '体质', newCON);
      scOut += `\n体质检定：D100=${conCheck}/${curCON} 失败，永久减少体质${conLoss}点（${curCON}→${newCON}）。`;

      const curSIZ = getAttr(mctx, '体型') || orig.siz || 50;
      const newHPMax = Math.floor((newCON + curSIZ) / 10);
      seal.vars.intSet(mctx, '体力上限', newHPMax);
      const curHpAfter = seal.vars.intGet(mctx, '体力');
      if (curHpAfter[1] && curHpAfter[0] > newHPMax) {
        seal.vars.intSet(mctx, '体力', newHPMax);
        scOut += `\n最大生命值重算：(CON${newCON}+SIZ${curSIZ})/10=${newHPMax}。`;
      }
    } else {
      let conLoss = rollDice(mctx, conSuccessExpr);
      if (halveLosses) conLoss = Math.floor(conLoss / 2);
      if (conLoss > 0) {
        const newCON = Math.max(0, curCON - conLoss);
        seal.vars.intSet(mctx, '体质', newCON);
        scOut += `\n体质检定：D100=${conCheck}/${curCON} 成功，仍扣除体质${conLoss}点（${curCON}→${newCON}）。`;
        const curSIZ = getAttr(mctx, '体型') || orig.siz || 50;
        const newHPMax = Math.floor((newCON + curSIZ) / 10);
        seal.vars.intSet(mctx, '体力上限', newHPMax);
        const curHpAfter = seal.vars.intGet(mctx, '体力');
        if (curHpAfter[1] && curHpAfter[0] > newHPMax) {
          seal.vars.intSet(mctx, '体力', newHPMax);
          scOut += `\n最大生命值重算：(CON${newCON}+SIZ${curSIZ})/10=${newHPMax}。`;
        }
      } else {
        scOut += `\n体质检定：D100=${conCheck}/${curCON} 成功。`;
      }
    }
  }

  // Flush combat HP back to character card + auto .bta end
  flushCombatHP(gid, mctx.player.userId, mctx);
  // Auto .bta end: clear all pending attacks & combat flag
  const targetsKey = `pending_targets_${gid}`;
  const targetsRaw = ext.storageGet(targetsKey);
  if (targetsRaw) {
    try {
      const tids = JSON.parse(targetsRaw);
      for (const tid of tids) {
        ext.storageSet(`pending_atk_${gid}_${tid}`, '');
      }
    } catch (e) {}
    ext.storageSet(targetsKey, '');
  }
  ext.storageSet(`pending_atk_${gid}`, '');  // old-format key
  seal.vars.intSet(mctx, '$gCombatActive', 0);

  // --- Level-up tracking (升级与熟练度) ---
  // .setab 0 mode: skip auto growth calculation per spec (section 壹: "此模式下成长不自动结算")
  const autoModeV = seal.vars.intGet(mctx, '$gAutoBattleMode');
  const autoMode = autoModeV[1] ? autoModeV[0] : 1;
  if (autoMode === 0) {
    scOut += '\n（.setab 0 模式：成长不自动结算）';
  } else if ((spellCount > 2) || (combatFlag > 0)) {
    let transCountV = seal.vars.intGet(mctx, '$gHsValidTransformCount');
    let transCount = (transCountV[1] ? transCountV[0] : 0) + 1;
    seal.vars.intSet(mctx, '$gHsValidTransformCount', transCount);

    // Level-up nodes: 1, 3, 6, 10, 15, 21, 28... (triangular T_n? Actually spec: 1,3,6,10,15,...)
    const levelUpNodes = new Set([1, 3, 6, 10, 15, 21, 28, 36, 45, 55]);
    if (levelUpNodes.has(transCount)) {
      const curLevelV = seal.vars.intGet(mctx, '等级');
      const newLevel = (curLevelV[1] ? curLevelV[0] : 1) + 1;
      seal.vars.intSet(mctx, '等级', newLevel);
      scOut += `\n\n✨ 升级！当前等级: ${newLevel}（第 ${transCount} 次有效变身）`;

      // Deep magic at levels 4,8,12...
      if (newLevel >= 4 && newLevel % 4 === 0) {
        scOut += `\n🔮 可解锁一个【深层魔法】（需kp确认）`;
      }
      // Shallow magic at levels 2,6,10...
      if (newLevel >= 2 && newLevel % 4 === 2) {
        scOut += `\n✨ 可解锁一个【浅层魔法】（需kp确认）`;
      }
      // Any level-up: pick one spell to gain +30 proficiency
      scOut += `\n📈 可选择一个已掌握魔法 +30 熟练度（请使用 .st 手动修改）`;
    }

    // Per-combat spell proficiency: each spell used gets +1d20
    const spellCountV2 = seal.vars.intGet(mctx, '$gHsSpellCount');
    if (spellCountV2[1] && spellCountV2[0] > 0) {
      const profGain = parseInt(seal.format(mctx, '{1d20}'), 10) || 10;
      scOut += `\n📊 本场战斗法术使用 ${spellCountV2[0]} 次，基础熟练度 +${profGain}（请手动分配到具体法术）`;
    }
  }

  // Restore original attributes
  seal.vars.intSet(mctx, '体力上限', orig.hpMax);
  seal.vars.intSet(mctx, '体力', restoredHP);
  seal.vars.intSet(mctx, '魔力上限', orig.mpMax);
  seal.vars.intSet(mctx, '魔力', restoredMP);
  seal.vars.intSet(mctx, '外貌', orig.app);
  seal.vars.intSet(mctx, '闪避', orig.dodge);
  seal.vars.intSet(mctx, '体格', orig.build);
  seal.vars.intSet(mctx, '行动力', orig.mov);
  seal.vars.intSet(mctx, '$gHsTransformed', 0);
  seal.vars.intSet(mctx, '$gHsSpellCount', 0);
  seal.vars.intSet(mctx, '$gHsCombatFlag', 0);

  // Restore ALL combat skills to their original values
  if (orig.combatSkills) {
    for (const s of ALL_COMBAT_SKILLS) {
      if (orig.combatSkills[s] !== undefined) {
        seal.vars.intSet(mctx, s, orig.combatSkills[s]);
      }
    }
  } else {
    // Backward compatibility: old storage format
    if (orig.brawl !== undefined) seal.vars.intSet(mctx, '斗殴', orig.brawl);
    if (orig.firearms !== undefined) seal.vars.intSet(mctx, '射击', orig.firearms);
  }

  ext.storageSet(key, '');

  const pn = seal.format(mctx, '{$t玩家}');
  let unhsOut = `${pn} 解除了魔法少女变身！\n\n` +
    `HP: ${curHP} → ${restoredHP}\n` +
    `MP: ${curMP} → ${restoredMP}\n` +
    `属性已恢复至变身前状态。` + scOut;
  seal.replyToSender(ctx, msg, unhsOut);
  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  .stsave  — 保存角色属性到 ncbridge BTA 分组
//  将当前角色卡的全部法术/属性数据序列化为 JSON 存储于 ext.storageSet，
//  供 logutil 后端通过 API 同步到 napcat bridge 的 battleauto 类别。
// ============================================================
const cmdStsave = seal.ext.newCmdItemInfo();
cmdStsave.name = 'stsave';
cmdStsave.help =
  '.stsave // 保存当前角色属性到 BTA 分组（供跨群使用）\n' +
  '保存内容：属性、技能、法术、魔法少女序号、升级数据\n' +
  '数据存储在 ext.storageSet 中，可通过 /api/bridge_save 同步到 ncbridge';
cmdStsave.allowDelegate = true;
cmdStsave.solve = (ctx, msg, cmdArgs) => {
  const mctx = seal.getCtxProxyFirst(ctx, cmdArgs) || ctx;
  const uid = mctx.player.userId;
  const pn = seal.format(mctx, '{$t玩家}');

  // Collect all character data
  const data = { userId: uid, name: pn, savedAt: new Date().toISOString() };

  // Base attributes
  const baseAttrs = ['力量','体型','体质','敏捷','外貌','教育','智力','意志','幸运',
                     '体力','体力上限','魔力','魔力上限','理智','行动力','体格','等级',
                     '魔法少女序号','回合行动数','cm','克苏鲁神话'];
  data.attrs = {};
  for (const a of baseAttrs) {
    const v = seal.vars.intGet(mctx, a);
    if (v[1]) data.attrs[a] = v[0];
  }

  // String attributes
  const strAttrs = ['伤害值','伤害值a','伤害值b'];
  data.strAttrs = {};
  for (const a of strAttrs) {
    const v = seal.vars.strGet(mctx, a);
    if (v[1]) data.strAttrs[a] = v[0];
  }

  // Combat skills
  data.skills = {};
  for (const s of ALL_COMBAT_SKILLS) {
    const v = seal.vars.intGet(mctx, s);
    if (v[1]) data.skills[s] = v[0];
  }
  // Also 闪避
  const dv = seal.vars.intGet(mctx, '闪避');
  if (dv[1]) data.skills['闪避'] = dv[0];

  // Spells (scan for 技能一 through 技能N keys)
  data.spells = {};
  for (let i = 1; i <= 20; i++) {
    const prefix = `技能${['零','一','二','三','四','五','六','七','八','九','十',
      '十一','十二','十三','十四','十五','十六','十七','十八','十九','二十'][i]}`;
    const spellData = {};
    let foundAny = false;
    // Try a few common keys
    const spellKeys = ['名称','级别','类别','时机','消耗mp','消耗san','熟练度','里程碑','简介'];
    for (const sk of spellKeys) {
      const sv = seal.vars.strGet(mctx, `${prefix}${sk}`);
      if (sv[1]) { spellData[sk] = sv[0]; foundAny = true; }
    }
    // Also scan int keys (categories a-f)
    const catLetters = ['a','b','c','d','e','f'];
    const catIntSuffixes = ['客体','作用半径','可调节性','成功率','成功率奖惩骰',
      '可反应性','可贯穿性','致死值','致死值优劣','附加效果时长','持续回合',
      '引发目标法术','引发延迟回合','制造个数','制造花费回合数','领域中心跟随','触发HP比例'];
    for (const letter of catLetters) {
      for (const suffix of catIntSuffixes) {
        const ik = '类别' + letter + suffix;
        const iv = seal.vars.intGet(mctx, prefix + ik);
        if (iv[1]) { spellData[ik] = iv[0]; foundAny = true; }
      }
    }
    // Scan category string keys (damage dice, shield values, heal amounts, etc.)
    const catStrSuffixes = ['伤害骰','附加效果','护盾值','回复hp','回复san','回复mp',
      '技能加减值','其他辅助效果a','辅助效果值a','召唤个数','召唤物模板','制造物模板',
      '每回合伤害骰','吸血比例','属性削减'];
    for (const letter of catLetters) {
      for (const suffix of catStrSuffixes) {
        const sk = '类别' + letter + suffix;
        const sv = seal.vars.strGet(mctx, prefix + sk);
        if (sv[1]) { spellData[sk] = sv[0]; foundAny = true; }
      }
    }
    // Also read 默认延续性 as int
    const persistV = seal.vars.intGet(mctx, `${prefix}默认延续性`);
    if (persistV[1]) { spellData['默认延续性'] = persistV[0]; foundAny = true; }

    if (foundAny) data.spells[i] = spellData;
  }

  // Scan craft templates (制造物模板)
  data.craftTemplates = {};
  for (let i = 1; i <= 10; i++) {
    const tmplPrefix = `制造物模板${CN_NUMS[i] || i}`;
    const nameV = seal.vars.strGet(mctx, `${tmplPrefix}名称`);
    const bareV = i === 1 ? seal.vars.strGet(mctx, '制造物模板') : [false, ''];
    if (!nameV[1] && !bareV[1]) continue;
    const tmpl = { name: nameV[1] ? nameV[0] : (bareV[1] ? bareV[0] : '') };
    const baseKeys = ['HP','MP','SAN','STR','CON','SIZ','DEX','APP','INT','POW','EDU'];
    for (const k of baseKeys) {
      const v = seal.vars.intGet(mctx, `${tmplPrefix}${k}`);
      if (v[1]) tmpl[k] = v[0];
    }
    tmpl.skills = [];
    for (let si = 1; si <= 3; si++) {
      const skV = seal.vars.strGet(mctx, `${tmplPrefix}技能${si}`);
      if (skV[1]) tmpl.skills.push(skV[0]);
    }
    data.craftTemplates[i] = tmpl;
  }

  // Scan summon templates (召唤物模板)
  data.summonTemplates = {};
  for (let i = 1; i <= 10; i++) {
    const tmplPrefix2 = `召唤物模板${CN_NUMS[i] || i}`;
    const nameV2 = seal.vars.strGet(mctx, `${tmplPrefix2}名称`);
    if (!nameV2[1] && i > 1) continue;
    const tmpl2 = { name: nameV2[1] ? nameV2[0] : (i === 1 ? (seal.vars.strGet(mctx, '召唤物模板')[1] ? seal.vars.strGet(mctx, '召唤物模板')[0] : '') : '') };
    if (!tmpl2.name) continue;
    const sumKeys = ['HP','MP','SAN','STR','CON','SIZ','DEX','APP','INT','POW','EDU','闪避','MOV','行动次数','可反击','可反应'];
    for (const k of sumKeys) {
      const v = seal.vars.intGet(mctx, `${tmplPrefix2}${k}`);
      if (v[1]) tmpl2[k] = v[0];
    }
    tmpl2.skills = [];
    for (let si = 1; si <= 3; si++) {
      const skV = seal.vars.strGet(mctx, `${tmplPrefix2}技能${si}`);
      if (skV[1]) tmpl2.skills.push(skV[0]);
    }
    data.summonTemplates[i] = tmpl2;
  }

  // Store under BTA key
  const btaKey = `bta_char_${uid}`;
  ext.storageSet(btaKey, JSON.stringify(data));
  // Also store a global index so backend can discover
  const indexRaw = ext.storageGet('bta_char_index');
  let index = [];
  if (indexRaw) { try { index = JSON.parse(indexRaw); } catch(e) {} }
  if (!index.includes(uid)) { index.push(uid); ext.storageSet('bta_char_index', JSON.stringify(index)); }

  seal.replyToSender(ctx, msg,
    `${pn} 的角色数据已保存到 BTA 分组！\n` +
    `包含 ${Object.keys(data.attrs).length} 项属性、${Object.keys(data.skills).length} 项技能、${Object.keys(data.spells).length} 个法术。\n` +
    `存储键: ${btaKey}`);
  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  .as <序号>  — 绑定魔法少女序号
//  将当前发言者绑定到指定魔法少女序号，后续 .ra/.st 等操作针对该角色卡。
//  序号输入支持阿拉伯数字（1,2,3...）或罗马数字（I,II,III...）。
// ============================================================
const cmdAs = seal.ext.newCmdItemInfo();
cmdAs.name = 'as';
cmdAs.help =
  '.as <序号> // 绑定魔法少女序号（如 .as Y1, .as 3, .as III）\n' +
  '在战斗中：.as Y1 按序号绑定，.as 3 按先攻列表编号绑定。\n' +
  '已加入时追加操控角色，不替换已有绑定。\n' +
  '绑定后，其他玩家可用 @你 来引用你的魔法少女序号。';
cmdAs.solve = (ctx, msg, cmdArgs) => {
  const val = cmdArgs.getArgN(1);
  if (!val) {
    seal.replyToSender(ctx, msg, '用法：.as <序号>（如 .as Y1, .as 3, .as III）');
    return seal.ext.newCmdExecuteResult(true);
  }
  const uid = ctx.player.userId;
  // Parse: support "Y1", "X0", "3", "III" etc.
  let serial = val;
  // Roman numeral conversion (simple I-V only for now)
  const romanMap = { 'i':1,'ii':2,'iii':3,'iv':4,'v':5,'vi':6,'vii':7,'viii':8,'ix':9,'x':10 };
  const lower = val.toLowerCase();
  if (romanMap[lower] !== undefined) serial = String(romanMap[lower]);

  // Store binding in ext.storageSet
  const bindKey = `bta_bind_${uid}`;
  ext.storageSet(bindKey, JSON.stringify({ serial, boundAt: Date.now() }));
  // Store reverse mapping for @ mention resolution
  const revKey = `bta_serial_${serial}`;
  const existingRaw = ext.storageGet(revKey);
  let existing = [];
  if (existingRaw) { try { existing = JSON.parse(existingRaw); } catch(e) {} }
  if (!existing.includes(uid)) { existing.push(uid); ext.storageSet(revKey, JSON.stringify(existing)); }

  seal.replyToSender(ctx, msg, `${seal.format(ctx, '{$t玩家}')} 已绑定为魔法少女序号【${serial}】`);

  // Check if in active battle — if so, immediately bind as controller
  const gid = ctx.group ? ctx.group.groupId : 'private';
  const abMode = parseInt(ext.storageGet(`setab_${gid}`)) || 0;
  const battleId = ext.storageGet(`pvp_battle_${gid}`);

  if (abMode === 2 && battleId) {
    // /setab2 mode: call server to bind controller
    // Detect numeric input → use init_index; otherwise use serial
    const isNumeric = /^\d+$/.test(val);
    const serverUrl = getPvpServerUrl() + '/api/pvp/' + battleId + '/bind';
    const sendPayload = {};
    sendPayload.player_id = uid;
    if (isNumeric) {
      sendPayload.init_index = parseInt(val);
    } else {
      sendPayload.serial = serial;
    }
    fetch(serverUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sendPayload),
    }).then(r => r.json()).then(result => {
      if (result.error) {
        seal.replyToSender(ctx, msg, `[.setab 2] 战斗中绑定失败: ${result.message}`);
      } else {
        const charName = result.char_name || serial;
        const bindKey2 = isNumeric ? `行动序号#${val}` : serial;
        let bindMsg = `${seal.format(ctx, '{$t玩家}')} 已接管【${bindKey2}】（${charName}）`;
        // Show all controlled characters
        if (result.my_characters && result.my_characters.length > 0) {
          const names = result.my_characters.map(c => `${c.name}${c.serial ? '(' + c.serial + ')' : ''}`).join(', ');
          bindMsg += `\n你当前控制: ${names}`;
        }
        seal.replyToSender(ctx, msg, bindMsg);
        if (result.auto_turns && result.auto_turns.length > 0) {
          const nodes = result.auto_turns.map((t, i) => {
            const label = (result.turn_labels && result.turn_labels[i]) || `行动 ${i+1}`;
            return `【${label}】\n${t}`;
          });
          sendForwardMessage(ctx, msg, nodes, 'AI 自动战斗');
        }
      }
    }).catch(err => {
      seal.replyToSender(ctx, msg, `[.setab 2] 战斗中绑定失败: ${err.message}`);
    });
  } else if (abMode === 1) {
    // /setab1 mode: update local controller list
    const ctrlKey = `bta_ctrl_${gid}_${serial}`;
    const existingCtrl = ext.storageGet(ctrlKey);
    let controllers = [];
    if (existingCtrl) {
      try { controllers = JSON.parse(existingCtrl); } catch(e) {}
    }
    if (!controllers.includes(uid)) {
      controllers.push(uid);
      ext.storageSet(ctrlKey, JSON.stringify(controllers));
      seal.replyToSender(ctx, msg, `${seal.format(ctx, '{$t玩家}')} 已接管【${serial}】（多名玩家可同时操控）`);
    }
  }

  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  .cm  — CM（克苏鲁神话）检定
//  掷 1d100 对比 灵感 + CM，出目越大越好（知识检定）。
//  kp 给出成功值。
// ============================================================
const cmdCm = seal.ext.newCmdItemInfo();
cmdCm.name = 'cm';
cmdCm.help =
  '.cm // 克苏鲁神话知识检定\n' +
  '掷 1d100 对比 灵感 + CM（出目越大越好）';
cmdCm.allowDelegate = true;
cmdCm.solve = (ctx, msg, cmdArgs) => {
  const mctx = seal.getCtxProxyFirst(ctx, cmdArgs) || ctx;
  const intel = getAttr(mctx, '智力') || getAttr(mctx, '灵感') || getAttr(mctx, 'int', 50);
  const cm = getAttr(mctx, 'cm') || getAttr(mctx, '克苏鲁神话', 0);
  const threshold = intel + cm;
  const roll = rollDice(mctx, '1d100');
  const pn = seal.format(mctx, '{$t玩家}');
  seal.replyToSender(ctx, msg,
    `${pn} 的 CM 检定：\n` +
    `D100=${roll} / 灵感(${intel})+CM(${cm})=${threshold}\n` +
    `（出目越大越好，kp给出成功值）`);
  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  kp GM 指令 — 扩展 .bta handler 处理以下子命令:
//    .bta setreaction X1 d/c  — 设定怪物默认反应
//    .bta new xxx              — 新建战斗模板
//    .bta map [10*10]          — 渲染/创建地图
//    .bta add Y12 A1           — 放置人物到地图
//    .bta save / import        — 保存/载入预设
//  这些在 makeBtaCmd 的 solve 开头统一处理。
// ============================================================
function handleKpCommand(ctx, msg, cmdArgs) {
  const args = cmdArgs.cleanArgs.split(/\s+/).filter(a => a.length > 0);
  if (args.length < 1) return false;
  const sub = args[0].toLowerCase();

  if (sub === 'start') {
    // .bta start → delegate to btastart
    seal.replyToSender(ctx, msg, '请使用 .btastart 或 .btastartfull 指令开始战斗。');
    return true;
  }
  if (sub === 'end') {
    // .bta end → delegate to btaend
    seal.replyToSender(ctx, msg, '请使用 .btaend 指令结束战斗。');
    return true;
  }

  if (sub === 'setreaction') {
    // .bta setreaction X1 d/c
    if (args.length < 3) {
      seal.replyToSender(ctx, msg, '用法：.bta setreaction <编号> <d/c>（如 .bta setreaction X1 d）');
      return true;
    }
    const npcId = args[1];
    const reactType = args[2].toLowerCase();
    if (reactType !== 'd' && reactType !== 'c') {
      seal.replyToSender(ctx, msg, '反应类型必须是 d（闪避）或 c（反击）');
      return true;
    }
    const gid = ctx.group ? ctx.group.groupId : 'private';
    const reKey = `bta_npc_reaction_${gid}`;
    let reactions = {};
    const raw = ext.storageGet(reKey);
    if (raw) { try { reactions = JSON.parse(raw); } catch(e) {} }
    reactions[npcId] = reactType;
    ext.storageSet(reKey, JSON.stringify(reactions));
    seal.replyToSender(ctx, msg, `NPC【${npcId}】默认反应已设为：${reactType === 'd' ? '闪避' : '反击'}`);
    return true;
  }

  if (sub === 'new') {
    const tplName = args[1] || 'default';
    const gid = ctx.group ? ctx.group.groupId : 'private';
    const tplKey = `bta_template_${gid}_${tplName}`;
    ext.storageSet(tplKey, JSON.stringify({
      name: tplName, createdAt: Date.now(),
      map: null, initiative: [], participants: {},
      npcReactions: {}
    }));
    seal.replyToSender(ctx, msg, `已创建战斗模板【${tplName}】（群: ${gid}）\n可通过 .bta import ${tplName} 载入。`);
    return true;
  }

  if (sub === 'int') {
    seal.replyToSender(ctx, msg, '用法：.bta int team=Y pos=F/M/B（或直接使用 .btaint 指令）\n' +
      'team: 阵营  pos: F(前部,入口+3) M(中部,入口+2) B(后部,入口+1)');
    return true;
  }

  if (sub === 'map') {
    const dimArg = args[1] || '10*10';
    const dimMatch = dimArg.match(/^(\d+)\*(\d+)$/);
    if (!dimMatch) {
      seal.replyToSender(ctx, msg, '用法：.bta map [宽*高]（如 .bta map 10*10）');
      return true;
    }
    const w = parseInt(dimMatch[1]), h = parseInt(dimMatch[2]);
    const gid = ctx.group ? ctx.group.groupId : 'private';
    const mapKey = `combat_map_${gid}`;
    const entryRow = Math.ceil(h / 2);
    const mapData = {
      width: w, height: h, entryCol: 'A', entryRow,
      obstacles: {}, occupants: {},
      createdAt: Date.now()
    };
    ext.storageSet(mapKey, JSON.stringify(mapData));
    // Render text map
    let mapOut = `地图 ${w}×${h}（入口: A${entryRow}）\n`;
    mapOut += '```\n';
    // Column headers
    mapOut += '   ';
    for (let c = 0; c < w; c++) mapOut += ` ${String.fromCharCode(65 + c)} `;
    mapOut += '\n';
    for (let r = h; r >= 1; r--) {
      mapOut += r.toString().padStart(2) + ' ';
      for (let c = 0; c < w; c++) {
        const coord = `${String.fromCharCode(65 + c)}${r}`;
        mapOut += r === entryRow && c === 0 ? '[门]' : '[  ]';
      }
      mapOut += '\n';
    }
    mapOut += '```\n';
    mapOut += '使用 .bta add <人物> <坐标> 放置参与者。';
    seal.replyToSender(ctx, msg, mapOut);
    return true;
  }

  if (sub === 'add') {
    if (args.length < 3) {
      seal.replyToSender(ctx, msg, '用法：.bta add <人物编号> <坐标>（如 .bta add Y12 A1）');
      return true;
    }
    const charId = args[1], coord = args[2].toUpperCase();
    const gid = ctx.group ? ctx.group.groupId : 'private';
    const mapKey = `combat_map_${gid}`;
    const raw = ext.storageGet(mapKey);
    if (!raw) {
      seal.replyToSender(ctx, msg, '请先使用 .bta map 创建地图！');
      return true;
    }
    const mapData = JSON.parse(raw);
    if (!mapData.occupants) mapData.occupants = {};
    // Faction obstacle check: different factions cannot share cells
    const existingOcc = mapData.occupants[coord];
    if (existingOcc) {
      const myFaction = String(charId).charAt(0).toUpperCase();
      const occFaction = String(existingOcc).charAt(0).toUpperCase();
      if (myFaction !== occFaction && /^[A-Z]$/.test(myFaction) && /^[A-Z]$/.test(occFaction)) {
        seal.replyToSender(ctx, msg, `${coord} 已被【${existingOcc}】（阵营${occFaction}）占据，不同阵营不可共格！`);
        return true;
      }
    }
    mapData.occupants[coord] = charId;
    ext.storageSet(mapKey, JSON.stringify(mapData));
    seal.replyToSender(ctx, msg, `已将【${charId}】放置到地图坐标 ${coord}。`);
    return true;
  }

  if (sub === 'save') {
    seal.replyToSender(ctx, msg, '当前地图状态已自动保存。使用 .bta import <名称> 可载入战斗模板。');
    return true;
  }

  if (sub === 'import') {
    const tplName = args[1] || 'default';
    const gid = ctx.group ? ctx.group.groupId : 'private';
    const tplKey = `bta_template_${gid}_${tplName}`;
    const raw = ext.storageGet(tplKey);
    if (!raw) {
      seal.replyToSender(ctx, msg, `未找到战斗模板【${tplName}】！请先 .bta new ${tplName}`);
      return true;
    }
    try {
      const tpl = JSON.parse(raw);
      if (tpl.map) ext.storageSet(`combat_map_${gid}`, JSON.stringify(tpl.map));
      seal.replyToSender(ctx, msg, `已载入战斗模板【${tplName}】（创建于 ${new Date(tpl.createdAt).toLocaleString()}）`);
    } catch(e) {
      seal.replyToSender(ctx, msg, '模板数据损坏，请重新创建。');
    }
    return true;
  }

  return false;  // not a kp command
}

// ============================================================
//  .setab 2 — Python HTTP 后端工具函数
// ============================================================

/** Get the PvP battle server URL from SealDice config (default: http://127.0.0.1:8889). */
function getPvpServerUrl() {
  const url = seal.ext.getStringConfig(ext, 'PvP战斗后端地址');
  return (url || 'http://127.0.0.1:8889').trim().replace(/\/+$/, '');
}

/** Collect COC attributes + runtime state from a character card for Python backend.
 *  Spells, damage dice, and special attrs come from Python chData — NOT sent here. */
function serializeCharacterForEngine(ctx) {
  const data = { uid: '', name: '', serial: '', attrs: {}, str_attrs: {} };
  data.uid = ctx.player.userId;
  // Serial number from .as binding
  const mgidV = seal.vars.intGet(ctx, '魔法少女序号');
  const mgid = mgidV[1] ? mgidV[0] : 0;
  if (mgid > 0) {
    data.serial = 'Y' + mgid;
    data.attrs['魔法少女序号'] = mgid;
  }
  // COC base 8
  const cocBase = ['力量','体质','体型','敏捷','外貌','教育','智力','意志','幸运'];
  for (const k of cocBase) {
    const v = seal.vars.intGet(ctx, k);
    if (v[1]) data.attrs[k] = v[0];
  }
  // Runtime state
  const runtime = ['体力','体力上限','魔力','魔力上限','理智','行动力','体格','等级',
                   '回合行动数','闪避','伤害贯穿','可反击','状态'];
  for (const k of runtime) {
    const v = seal.vars.intGet(ctx, k);
    if (v[1]) data.attrs[k] = v[0];
  }
  // Combat skills (player's actual values)
  for (const sk of ALL_COMBAT_SKILLS) {
    const v = seal.vars.intGet(ctx, sk);
    if (v[1]) data.attrs[sk] = v[0];
  }
  // Name
  const nameResult = seal.format(ctx, '{$t玩家}');
  data.name = nameResult || '未知';
  return data;
}

/** Serialize all battle state for sending to Python backend. */
function serializeBattleState(gid) {
  const mapRaw = ext.storageGet(`combat_map_${gid}`);
  let mapData = null;
  if (mapRaw) { try { mapData = JSON.parse(mapRaw); } catch(e) {} }

  const initList = getInitiative(gid);
  const actions = getActions(gid);

  // Collect character data from all initiative participants
  // Note: in SealDice we can only get ctx for the current player.
  // For other players, we rely on data already in initiative + storage.
  const characters = {};

  return {
    group_id: String(gid),
    map: mapData,
    initiative: initList,
    actions: actions,
    characters: characters,
  };
}

/** HTTP POST to PvP backend. Returns parsed JSON or {error:true, message}. */
async function pvpFetch(endpoint, data) {
  try {
    const baseUrl = getPvpServerUrl();
    const resp = await fetch(`${baseUrl}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!resp.ok) {
      try {
        const err = await resp.json();
        return { error: true, message: err.message || `HTTP ${resp.status}` };
      } catch(e) {
        return { error: true, message: `服务器错误 HTTP ${resp.status}` };
      }
    }
    return await resp.json();
  } catch(e) {
    return { error: true, message: '战斗服务器无响应，请确认 battle_http_server.py 已启动（端口8889）。' };
  }
}

/** Apply HP/MP changes from Python response to local JS storage. */
function applyServerChanges(gid, result) {
  if (!result) return;
  // HP changes
  if (result.hp_changes) {
    const hpStore = getCombatHP(gid);
    for (const [uid, hp] of Object.entries(result.hp_changes)) {
      hpStore[uid] = hp;
    }
    setCombatHP(gid, hpStore);
  }
  // Effects
  if (result.effects !== undefined) {
    ext.storageSet(`combat_effects_${gid}`, result.effects.length > 0 ? JSON.stringify(result.effects) : '');
  }
}

/** Get the current auto-battle mode. */
function getAutoMode(ctx) {
  const v = seal.vars.intGet(ctx, '$gAutoBattleMode');
  return v[1] ? v[0] : 1;  // default to 1
}

// ============================================================
//  .setab 1 战斗引擎 — 状态管理
// ============================================================

/** Get current combat state for a group */
function getCombatState(groupId) {
  const raw = ext.storageGet(`combat_state_${groupId}`);
  if (!raw) return null;
  try { return JSON.parse(raw); } catch(e) { return null; }
}
function setCombatState(groupId, state) {
  ext.storageSet(`combat_state_${groupId}`, JSON.stringify(state));
}

/** Get initiative order for a group */
function getInitiative(groupId) {
  const raw = ext.storageGet(`combat_initiative_${groupId}`);
  if (!raw) return [];
  try { return JSON.parse(raw); } catch(e) { return []; }
}
function setInitiative(groupId, list) {
  ext.storageSet(`combat_initiative_${groupId}`, JSON.stringify(list));
}

/** Get per-character action counts */
function getActions(groupId) {
  const raw = ext.storageGet(`combat_actions_${groupId}`);
  if (!raw) return {};
  try { return JSON.parse(raw); } catch(e) { return {}; }
}
function setActions(groupId, data) {
  ext.storageSet(`combat_actions_${groupId}`, JSON.stringify(data));
}

/** Format a grid coordinate e.g. A1 -> {col:0, row:0} */
function parseCoord(s) {
  const m = s.match(/^([A-Z])(\d+)$/i);
  if (!m) return null;
  return { col: m[1].toUpperCase().charCodeAt(0) - 65, row: parseInt(m[2]) - 1 };
}
function formatCoord(col, row) { return `${String.fromCharCode(65 + col)}${row + 1}`; }

/** Check if a target is within melee range (5x5 square centered on attacker).
 *  Chebyshev distance ≤ 2 in both axes. */
function isInMeleeRange(attackerCoord, targetCoord) {
  const a = parseCoord(attackerCoord);
  const t = parseCoord(targetCoord);
  if (!a || !t) return false;
  return Math.abs(a.col - t.col) <= 2 && Math.abs(a.row - t.row) <= 2;
}

/** Get a character's faction from the initiative list.
 *  Returns team string ('Y','X','Z',etc.) or null if not found. */
function getCharFaction(groupId, userId) {
  const initList = getInitiative(groupId);
  const entry = initList.find(e => e.userId === userId);
  return entry ? entry.team : null;
}

/** Check if a cell can be entered by a character, considering faction-based obstacles.
 *  Different-faction characters block each other's cells. */
function canEnterCell(mapData, groupId, coord, userId) {
  if (!mapData || !mapData.occupants) return true;
  const occ = mapData.occupants[coord];
  if (!occ) return true;  // cell is empty
  // Check if occupant is the same user
  if (occ === userId) return true;
  // Check faction compatibility
  const myFaction = getCharFaction(groupId, userId);
  if (!myFaction) return true;  // not in initiative, allow
  // Find the occupant's faction — occupant might be stored as userId or as serial
  let occUserId = occ;
  // If occ is a serial like "Y1", find the userId from initiative
  const initList = getInitiative(groupId);
  const occEntry = initList.find(e => e.userId === occ || (e.name && e.name === occ));
  if (occEntry) occUserId = occEntry.userId;
  else {
    // occ might be a serial or summon/craft ID — look it up in map occupants
    // For NPCs/summons, check if the stored value contains faction prefix
    const occAsStr = String(occ);
    if (occAsStr.startsWith(myFaction)) return true;  // same faction
    // If it's a different faction prefix, block
    const factionPrefixes = ['Y','X','Z'];
    for (const fp of factionPrefixes) {
      if (fp !== myFaction && occAsStr.startsWith(fp)) return false;
    }
    return true;  // allow for non-faction entities (summons, crafts)
  }
  const occFaction = getCharFaction(groupId, occUserId);
  if (!occFaction) return true;
  return occFaction === myFaction;
}

/** Format map state as logai-compatible markdown for image rendering */
function formatMapForLogai(groupId) {
  const raw = ext.storageGet(`combat_map_${groupId}`);
  if (!raw) return '';
  const m = JSON.parse(raw);
  let md = `【标题】战斗地图 (${m.width}×${m.height})\n\n`;
  md += '|   ';
  for (let c = 0; c < m.width; c++) md += `| ${String.fromCharCode(65 + c)} `;
  md += '|\n';
  md += '|';
  for (let c = 0; c <= m.width; c++) md += '---|';
  md += '\n';
  for (let r = m.height - 1; r >= 0; r--) {
    md += `| **${r+1}** `;
    for (let c = 0; c < m.width; c++) {
      const coord = formatCoord(c, r);
      if (m.obstacles[coord]) md += '| ██ ';
      else if (m.occupants[coord]) md += `| ${m.occupants[coord]} `;
      else md += '|   ';
    }
    md += '|\n';
  }
  return md;
}

/** Try to render map as image via logai backend. Falls back to text grid.
 *  Requires logai_server_release.py running on localhost:8000. */
async function renderMapImage(groupId) {
  const md = formatMapForLogai(groupId);
  if (!md) return '暂无地图。';
  try {
    // Attempt to call logai image API
    if (typeof fetch !== 'undefined') {
      const resp = await fetch('http://127.0.0.1:8000/api/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: md + '\n【主题：赛博】', title: 'combat_map', group_id: groupId }),
      });
      if (resp.ok) {
        const data = await resp.json();
        return `[地图图片] ${data.url || '(正在生成...)'}\n(文本版见下方)\n${renderMap(groupId)}`;
      }
    }
  } catch (e) {
    // Fall back to text rendering
  }
  return renderMap(groupId);
}

/** Render combat map as text grid */
function renderMap(groupId) {
  const raw = ext.storageGet(`combat_map_${groupId}`);
  if (!raw) return '暂无地图。请使用 .bta map [宽*高] 创建。';
  const m = JSON.parse(raw);
  let out = `地图 ${m.width}×${m.height} 入口: A${m.entryRow}\n\`\`\`\n`;
  out += '   ';
  for (let c = 0; c < m.width; c++) out += ` ${String.fromCharCode(65 + c)} `;
  out += '\n';
  for (let r = m.height - 1; r >= 0; r--) {
    out += `${(r+1).toString().padStart(2)} `;
    for (let c = 0; c < m.width; c++) {
      const coord = formatCoord(c, r);
      if (m.obstacles[coord]) { out += '[██]'; }
      else if (m.occupants[coord]) {
        const occ = m.occupants[coord];
        out += `[${occ.length > 3 ? occ.slice(0,3) : occ.padEnd(3)}]`;  // truncate to 3 chars
      } else { out += '[  ]'; }
    }
    out += `\n`;
  }
  out += '```';
  return out;
}

// ============================================================
//  .bta int  — 加入战斗（先攻初始化）
//  语法: .bta int team=Y pos=F/M/B card=本群卡
// ============================================================
const cmdBtaInt = seal.ext.newCmdItemInfo();
cmdBtaInt.name = 'bta int';
cmdBtaInt.help =
  '.bta int team=Y pos=F/M/B // 加入战斗，自动投掷敏捷确定先攻\n' +
  'team: 阵营标识 (Y=友方, X/Z=敌方)\n' +
  'pos: F(前部,入口+3) M(中部,入口+2) B(后部,入口+1)\n' +
  '使用 .bta start 开始战斗';
cmdBtaInt.solve = (ctx, msg, cmdArgs) => {
  const mctx = seal.getCtxProxyFirst(ctx, cmdArgs) || ctx;
  const gid = ctx.group ? ctx.group.groupId : 'private';

  // Parse args
  const args = (cmdArgs.cleanArgs || '').split(/\s+/).filter(a => a.length > 0);
  let team = 'Y', pos = 'M', card = '本群卡';
  for (const a of args) {
    if (/^team=/i.test(a)) team = a.split('=')[1].toUpperCase();
    if (/^pos=/i.test(a)) pos = a.split('=')[1].toUpperCase();
    if (/^card=/i.test(a)) card = a.split('=')[1];
  }

  // Validate position & map
  const mapRaw = ext.storageGet(`combat_map_${gid}`);
  if (!mapRaw) {
    seal.replyToSender(ctx, msg, '请先使用 .bta map 创建地图！');
    return seal.ext.newCmdExecuteResult(true);
  }
  const mapData = JSON.parse(mapRaw);
  const entryRow = mapData.entryRow || Math.ceil(mapData.height / 2);
  const entryCoord = formatCoord(0, entryRow - 1);

  // Determine coordinate: F/M/B preset OR custom coordinate string (e.g. A5)
  let coord;
  const posUpper = pos.toUpperCase();
  if (/^[A-Z]\d+$/i.test(posUpper)) {
    // Custom coordinate
    const parsed = parseCoord(posUpper);
    if (!parsed || parsed.col >= mapData.width || parsed.row >= mapData.height) {
      seal.replyToSender(ctx, msg, `坐标 ${posUpper} 超出地图范围 (${mapData.width}×${mapData.height})！`);
      return seal.ext.newCmdExecuteResult(true);
    }
    coord = posUpper;
  } else {
    const posMap = { 'F': 3, 'M': 2, 'B': 1 };
    const offset = posMap[posUpper] || 2;
    const targetCol = 0;
    const targetRow = Math.min(mapData.height - 1, entryRow - 1 + offset);
    coord = formatCoord(targetCol, targetRow);
  }

  // Validate distance from entry ≤ MOV
  const parsedCoord = parseCoord(coord);
  const parsedEntry = parseCoord(entryCoord);
  const mov = getAttr(mctx, '行动力', 8);
  if (parsedCoord && parsedEntry) {
    const dist = Math.abs(parsedCoord.col - parsedEntry.col) + Math.abs(parsedCoord.row - parsedEntry.row);
    if (dist > mov) {
      seal.replyToSender(ctx, msg, `位置 ${coord} 距离入口 ${entryCoord} 为 ${dist} 格，超过行动力 ${mov}！`);
      return seal.ext.newCmdExecuteResult(true);
    }
  }

  // Roll COC DEX for initiative (COC skill check against DEX)
  const dex = getAttr(mctx, '敏捷', 50);
  const { result: initRoll, detail: bpDetail } = rollD100(ctx, '');
  const initRank = successRank(initRoll, dex);
  const userId = mctx.player.userId;
  const pn = seal.format(mctx, '{$t玩家}');

  // Update initiative list
  let initList = getInitiative(gid);
  // Remove previous entry for this user
  initList = initList.filter(e => e.userId !== userId);
  initList.push({ userId, name: pn, team, pos, dex, initRoll, initRank, coord, joinedAt: Date.now() });
  // Sort by initRank descending (大成功 first), then dex, then initRoll
  initList.sort((a, b) => b.initRank - a.initRank || b.dex - a.dex || b.initRoll - a.initRoll);
  setInitiative(gid, initList);

  // Check faction obstacle: different factions cannot share the same cell
  if (!mapData.occupants) mapData.occupants = {};
  if (!canEnterCell(mapData, gid, coord, userId)) {
    seal.replyToSender(ctx, msg, `${coord} 已被不同阵营的角色占据，无法在此格加入战斗！`);
    return seal.ext.newCmdExecuteResult(true);
  }

  // Place on map
  mapData.occupants[coord] = `${team}${initList.length}`;
  // Actually use the user's serial from binding
  const bindRaw = ext.storageGet(`bta_bind_${userId}`);
  if (bindRaw) {
    try {
      const bind = JSON.parse(bindRaw);
      mapData.occupants[coord] = bind.serial;
    } catch(e) {}
  }
  ext.storageSet(`combat_map_${gid}`, JSON.stringify(mapData));

  // Init combat HP
  initCombatHP(gid, userId, mctx, userId, mctx);

  seal.replyToSender(ctx, msg,
    `${pn} 已加入战斗！\n` +
    `阵营: ${team}  位置: ${pos}(${coord})  敏捷: ${dex}  先攻检定: ${initRoll}\n` +
    `当前先攻表 (${initList.length}人):\n` +
    initList.map((e, i) => `  ${i+1}. ${e.name} [${e.team}] DEX${e.dex}+${e.initRoll}  ${e.coord}`).join('\n'));
  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  .bta start (增强版) — 开始战斗
//  锁定先攻表，自动变身，初始化行动次数
// ============================================================
const cmdBtaStartFull = seal.ext.newCmdItemInfo();
cmdBtaStartFull.name = 'btastartfull';
cmdBtaStartFull.help = '.btastartfull // .setab 1 模式开始战斗（初始化先攻与行动次数）\n等同于 .btastart + 先攻锁定 + 行动数分配';
cmdBtaStartFull.solve = (ctx, msg, cmdArgs) => {
  const mctx = seal.getCtxProxyFirst(ctx, cmdArgs) || ctx;
  const gid = mctx.group ? mctx.group.groupId : 'private';

  // Run the basic btastart logic
  const snapKey = `bta_snapshot_${mctx.player.userId}`;
  const snapshot = {};
  const baseAttrs = ['力量','体型','体质','敏捷','外貌','教育','智力','意志','幸运'];
  for (const a of baseAttrs) {
    const v = seal.vars.intGet(mctx, a); snapshot[a] = v[1] ? v[0] : 0;
  }
  for (const s of ALL_COMBAT_SKILLS) {
    const v = seal.vars.intGet(mctx, s); snapshot[s] = v[1] ? v[0] : 0;
  }
  snapshot['闪避'] = getAttr(mctx, '闪避');
  snapshot['体力'] = getAttr(mctx, '体力', 10);
  snapshot['体力上限'] = getAttr(mctx, '体力上限', 10);
  snapshot['魔力'] = getAttr(mctx, '魔力') || getAttr(mctx, 'mp', 10);
  snapshot['魔力上限'] = getAttr(mctx, '魔力上限') || getAttr(mctx, 'mp', 10);
  snapshot['理智'] = getAttr(mctx, '理智') || getAttr(mctx, 'san', 50);
  ext.storageSet(snapKey, JSON.stringify(snapshot));
  seal.vars.intSet(mctx, '$gCombatActive', 1);
  initCombatHP(gid, mctx.player.userId, mctx, mctx.player.userId, mctx);

  // .setab 1 extras: initialize actions
  const initList = getInitiative(gid);
  let actions = getActions(gid);
  for (const e of initList) {
    if (!actions[e.userId]) {
      actions[e.userId] = { 附加: 3, 主动: 2 };
    }
  }
  setActions(gid, actions);

  // Auto-transform all non-transformed participants
  for (const e of initList) {
    const uid2 = e.userId;
    // We can't easily get ctx for other users, so skip auto-transform here
    // (in practice, each player should .hs themselves before .bta start)
  }

  // Set combat state
  setCombatState(gid, { phase: 'active', round: 1, activeIndex: 0 });

  const pn = seal.format(mctx, '{$t玩家}');
  let out = `${pn} 的战斗已开始！\n`;
  out += `先攻表 (${initList.length}人):\n`;
  out += initList.map((e, i) => `  ${i+1}. ${e.name} [${e.team}] ${e.coord}  附加×3 主动×2`).join('\n');
  out += `\n\n` + renderMap(gid);
  out += `\n\n当前回合: 1 | 行动顺序: ${initList[0] ? initList[0].name : '(无)'}\n`;
  out += `使用 .sN 释放技能  .a m 坐标 移动  .a sN 附加动作技能`;
  seal.replyToSender(ctx, msg, out);
  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  .btastartfull2  — .setab 2 PvP 模式开始战斗
//  使用 Python HTTP 后端
// ============================================================
const cmdBtaStartFull2 = seal.ext.newCmdItemInfo();
cmdBtaStartFull2.name = 'btastartfull2';
cmdBtaStartFull2.help =
  '.btastartfull2 // .setab 2 PvP模式开始战斗（Python后端）\n' +
  '.btast / .btastt2  // 简写别名\n' +
  '.btast TEAM1 vs TEAM2  // 多人PvP格式：两队使用空格分隔的序号或TN战队别名\n' +
  '使用前确保 battle_http_server.py 已启动';
cmdBtaStartFull2.solve = (ctx, msg, cmdArgs) => {
  const mctx = seal.getCtxProxyFirst(ctx, cmdArgs) || ctx;
  const gid = mctx.group ? mctx.group.groupId : 'private';
  const rawArgs = (cmdArgs.cleanArgs || '').trim();

  // ── New multi-PvP format: .btast TEAM1 vs TEAM2 ──
  const vsMatch = rawArgs.match(/^(.+)\s+vs\s+(.+)$/i);
  if (vsMatch) {
    const leftTokens = vsMatch[1].split(/\s+/).filter(t => t.length > 0);
    const rightTokens = vsMatch[2].split(/\s+/).filter(t => t.length > 0);

    if (leftTokens.length < 1 || rightTokens.length < 1) {
      seal.replyToSender(ctx, msg, '用法：.btast TEAM1的全体 vs TEAM2的全体\n每侧至少1个角色序号或战队编号。');
      return seal.ext.newCmdExecuteResult(true);
    }

    // Resolve TN aliases from local team cache
    const teamCacheRaw = ext.storageGet('pvp_teams_cache');
    let teamCache = {};
    if (teamCacheRaw) { try { teamCache = JSON.parse(teamCacheRaw); } catch(e) {} }

    function resolveTokens(tokens) {
      const result = [];
      for (const t of tokens) {
        const upper = t.toUpperCase();
        if (teamCache[upper] && Array.isArray(teamCache[upper].members)) {
          result.push(...teamCache[upper].members);
        } else {
          result.push(upper);
        }
      }
      return result;
    }

    const teamA = resolveTokens(leftTokens);
    const teamB = resolveTokens(rightTokens);

    // Build player_bindings: use reverse mappings from .as command.
    // .as stores bta_serial_<serial> → [userId, ...] for each binding,
    // so we can collect ALL player bindings without requiring .bta int.
    const playerBindings = {};
    const allSerials = [...teamA, ...teamB];
    for (const serial of allSerials) {
      const revKey = `bta_serial_${serial}`;
      const revRaw = ext.storageGet(revKey);
      if (!revRaw) continue;
      try {
        const userIds = JSON.parse(revRaw);
        if (!Array.isArray(userIds)) continue;
        for (const uid of userIds) {
          if (!playerBindings[uid]) playerBindings[uid] = [];
          if (!playerBindings[uid].includes(serial)) {
            playerBindings[uid].push(serial);
          }
        }
      } catch(e) {}
    }

    const requestData = {
      group_id: String(gid),
      mode: 'multi_pvp',
      team_a: teamA,
      team_b: teamB,
      player_bindings: playerBindings,
      map_size: '10x10',
    };

    const aDisp = teamA.join('+');
    const bDisp = teamB.join('+');
    seal.replyToSender(ctx, msg, `Y队：${aDisp} vs X队：${bDisp}\n正在连接 Python 后端...`);

    pvpFetch('/api/pvp/create', requestData).then(result => {
      if (result.error) {
        seal.replyToSender(ctx, msg, `[.setab 2] ${result.message}`);
        return;
      }
      ext.storageSet(`pvp_battle_${gid}`, result.battle_id);
      if (result.initiative) setInitiative(gid, result.initiative);
      if (result.map) ext.storageSet(`combat_map_${gid}`, JSON.stringify(result.map));
      // Display
      let out = result.output || '';
      if (result.controlled_characters) {
        out += '\n\n【玩家绑定】';
        for (const [pid, chars] of Object.entries(result.controlled_characters)) {
          const names = chars.map(c => c.name).join(', ');
          out += `\n  ${pid}: ${names}`;
        }
      }
      seal.replyToSender(ctx, msg, out);
      // Sync HP
      if (result.characters) {
        const hpStore = {};
        for (const [uid, info] of Object.entries(result.characters)) {
          hpStore[uid] = info.hp;
          hpStore['mp_' + uid] = info.mp;
          hpStore['san_' + uid] = info.san;
        }
        setCombatHP(gid, hpStore);
      }
    });

    return seal.ext.newCmdExecuteResult(true);
  }

  // ── Old PvP format: .btastartfull2 (no args, uses existing map/initiative) ──

  // Collect player character data
  const playerData = serializeCharacterForEngine(ctx);

  // Build request: send map, initiative, and character data
  const mapRaw = ext.storageGet(`combat_map_${gid}`);
  let mapData = null;
  if (mapRaw) { try { mapData = JSON.parse(mapRaw); } catch(e) {} }
  if (!mapData) {
    seal.replyToSender(ctx, msg, '请先使用 .bta map 创建地图！或使用 .btast Y1 Y2 vs Y3 Y4 格式。');
    return seal.ext.newCmdExecuteResult(true);
  }

  const initList = getInitiative(gid);
  if (initList.length < 2) {
    seal.replyToSender(ctx, msg, '至少需要2人加入战斗（使用 .bta int）！或使用 .btast Y1 Y2 vs Y3 Y4 格式。');
    return seal.ext.newCmdExecuteResult(true);
  }

  // Collect character data for all participants
  const characters = {};
  // We can only get ctx for the current player. For other players,
  // we send the basic info we have from initiative.
  for (const e of initList) {
    if (e.userId === ctx.player.userId) {
      characters[e.userId] = playerData;
    } else {
      // For other players: minimal data — Python fills from chData by name/serial
      characters[e.userId] = {
        uid: e.userId,
        name: e.name || e.userId,
        serial: '',
        attrs: { '敏捷': e.dex || 50 },
        str_attrs: {},
      };
    }
  }

  const requestData = {
    group_id: String(gid),
    mode: 'pvp',
    map: mapData,
    initiative: initList,
    characters: characters,
  };

  seal.replyToSender(ctx, msg, '正在连接 Python 后端...');
  pvpFetch('/api/pvp/create', requestData).then(result => {
    if (result.error) {
      seal.replyToSender(ctx, msg, `[.setab 2] ${result.message}`);
      return;
    }
    // Store battle_id
    ext.storageSet(`pvp_battle_${gid}`, result.battle_id);
    // Sync state
    if (result.initiative) setInitiative(gid, result.initiative);
    if (result.map) ext.storageSet(`combat_map_${gid}`, JSON.stringify(result.map));
    // Display initial info
    seal.replyToSender(ctx, msg, result.output + '\n\n' + renderMap(gid));
    // Sync HP to local storage
    if (result.characters) {
      const hpStore = {};
      for (const [uid, info] of Object.entries(result.characters)) {
        hpStore[uid] = info.hp;
        hpStore['mp_' + uid] = info.mp;
        hpStore['san_' + uid] = info.san;
      }
      setCombatHP(gid, hpStore);
    }
  });

  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  .btastartfullai <对手编号(s)> as <自己编号> [with <队友编号(s)>]
//  .setab 2 PvE 模式 — 使用 chData 数据，玩家操控指定角色 vs 指定对手
//  所有角色数据（HP/MP/技能等）均来自 characters_data_pvp.py（chData）
// ============================================================
const cmdBtaStartFullAI = seal.ext.newCmdItemInfo();
cmdBtaStartFullAI.name = 'btastartfullai';
cmdBtaStartFullAI.help =
  '.btastartfullai <对手编号(s)> as <自己编号> [with <队友编号(s)>]\n' +
  '  <对手编号(s)> : 敌方队伍魔法少女编号（如 Y1 Y2）\n' +
  '  as <自己编号> : 你操控的角色（该角色的所有数据来自 chData）\n' +
  '  with <队友编号(s)> : 追加AI队友\n' +
  '  所有角色数据均来自 chData，不使用你的角色卡数据。\n' +
  '例:\n' +
  '  .btastartfullai Y1 as Y2          // 你操控Y2 vs 对手Y1\n' +
  '  .btastartfullai Y3 as Y1 with Y2  // 你操控Y1 + AI队友Y2 vs 对手Y3\n' +
  '  .btastartfullai Y12 Y1 as Y9      // 你操控Y9 vs 对手Y12+Y1';
cmdBtaStartFullAI.solve = (ctx, msg, cmdArgs) => {
  const gid = ctx.group ? ctx.group.groupId : 'private';
  const rawArgs = (cmdArgs.cleanArgs || '').split(/\s+/).filter(a => a.length > 0);

  // ── Parse syntax: <对手编号(s)> as <自己编号> [with <队友编号(s)>] ──
  const asIdx = rawArgs.findIndex(a => a.toLowerCase() === 'as');
  const withIdx = rawArgs.findIndex(a => a.toLowerCase() === 'with');

  // Serial numbers before 'as' or 'with' → opponents
  let mainEnd = rawArgs.length;
  if (asIdx >= 0) mainEnd = Math.min(mainEnd, asIdx);
  if (withIdx >= 0) mainEnd = Math.min(mainEnd, withIdx);
  const opponentSerials = rawArgs.slice(0, mainEnd).filter(a => /^[A-Za-z]\d+$/i.test(a));

  // 'as' designates which chData character the player IS
  let selfSerial = '';
  if (asIdx >= 0 && asIdx + 1 < rawArgs.length) {
    const candidate = rawArgs[asIdx + 1];
    if (/^[A-Za-z]\d+$/i.test(candidate)) {
      selfSerial = candidate.toUpperCase();
    }
  }
  // Default: if no 'as', use the first opponent serial as self (backward compat)
  if (!selfSerial && opponentSerials.length > 0) {
    selfSerial = opponentSerials[0];
    // Remove from opponents so it's not both self and opponent
    opponentSerials.splice(0, 1);
  }

  // 'with' adds AI allies (from chData)
  let allySerials = [];
  if (withIdx >= 0) {
    const rest = rawArgs.slice(withIdx + 1);
    for (const a of rest) {
      if (a.toLowerCase() === 'as') break;
      if (/^[A-Za-z]\d+$/i.test(a)) allySerials.push(a.toUpperCase());
    }
  }

  // Need at least self
  if (!selfSerial) {
    seal.replyToSender(ctx, msg,
      '请指定魔法少女编号。\n' +
      '用法：.btastartfullai <对手编号(s)> as <自己编号> [with <队友编号(s)>]\n' +
      '例：.btastartfullai Y12 as Y9  /  .btastartfullai Y3 as Y1 with Y2');
    return seal.ext.newCmdExecuteResult(true);
  }

  // If no opponents specified, opponent list is empty → Python will generate random opponents
  // Default 10x10 map
  const mapData = { width: 10, height: 10, entryRow: 5, obstacles: {}, occupants: {} };

  const requestData = {
    group_id: String(gid),
    mode: 'pve',
    map: mapData,
    opponent_serials: opponentSerials.map(s => s.toUpperCase()),
    self_serial: selfSerial.toUpperCase(),
    ally_serials: allySerials,
    player_uid: ctx.player.userId,
  };

  const oppStr = opponentSerials.length > 0 ? opponentSerials.join(' ') : '随机';
  seal.replyToSender(ctx, msg,
    `对手: ${oppStr} | 玩家: ${selfSerial}` +
    (allySerials.length > 0 ? ` | 队友: ${allySerials.join(' ')}` : '') +
    `\n（所有角色数据来自 chData）\n正在连接 Python 后端...`);

  pvpFetch('/api/pvp/create', requestData).then(result => {
    if (result.error) {
      seal.replyToSender(ctx, msg, `[.setab 2] ${result.message}`);
      return;
    }
    ext.storageSet(`pvp_battle_${gid}`, result.battle_id);
    ext.storageSet(`pvp_human_${gid}`, result.human_uid || ctx.player.userId);
    if (result.initiative) setInitiative(gid, result.initiative);
    if (result.map) ext.storageSet(`combat_map_${gid}`, JSON.stringify(result.map));
    seal.replyToSender(ctx, msg, result.output);
    if (result.characters) {
      const hpStore = {};
      for (const [uid, info] of Object.entries(result.characters)) {
        hpStore[uid] = info.hp;
        hpStore['mp_' + uid] = info.mp;
      }
      setCombatHP(gid, hpStore);
    }
  });

  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  .bteam new/list/delete  — 固定战队管理
// ============================================================
const cmdBteam = seal.ext.newCmdItemInfo();
cmdBteam.name = 'bteam';
cmdBteam.help =
  '.bteam new <名称> <序号...>  // 创建固定战队\n' +
  '.bteam list                  // 查看全部战队\n' +
  '.bteam delete <TN>           // 删除自己创建的战队\n' +
  '战队创建后可用 TN（如 T1）在 .btast 中代替成员列表。';
cmdBteam.solve = (ctx, msg, cmdArgs) => {
  const args = (cmdArgs.cleanArgs || '').split(/\s+/).filter(a => a.length > 0);
  const sub = (args[0] || '').toLowerCase();
  const playerId = ctx.player.userId;

  if (sub === 'new') {
    const name = args[1] || '';
    const members = args.slice(2).map(s => s.toUpperCase()).filter(s => /^[A-Z]?\d+$/i.test(s));
    if (!name || members.length < 1) {
      seal.replyToSender(ctx, msg, '用法：.bteam new <名称> <序号1> <序号2> ...\n例：.bteam new 红队 Y1 Y2 Y3');
      return seal.ext.newCmdExecuteResult(true);
    }
    pvpFetch('/api/pvp/team/create', {
      player_id: playerId,
      name: name,
      members: members,
    }).then(result => {
      if (result.error) {
        seal.replyToSender(ctx, msg, `[战队] ${result.message}`);
        return;
      }
      const cacheRaw = ext.storageGet('pvp_teams_cache');
      let cache = {};
      if (cacheRaw) { try { cache = JSON.parse(cacheRaw); } catch(e) {} }
      cache[result.team_id] = { name: result.name, members: result.members };
      ext.storageSet('pvp_teams_cache', JSON.stringify(cache));
      seal.replyToSender(ctx, msg,
        `战队创建成功：${result.team_id}（${result.name}）\n成员：${result.members.join(' ')}`);
    });

  } else if (sub === 'list') {
    const baseUrl = getPvpServerUrl();
    fetch(`${baseUrl}/api/pvp/team/list`, { method: 'GET' })
      .then(r => r.json())
      .then(result => {
        if (result.error) {
          seal.replyToSender(ctx, msg, `[战队] ${result.message}`);
          return;
        }
        const cache = {};
        for (const t of (result.teams || [])) {
          cache[t.id] = { name: t.name, members: t.members };
        }
        ext.storageSet('pvp_teams_cache', JSON.stringify(cache));
        if (!result.teams || result.teams.length === 0) {
          seal.replyToSender(ctx, msg, '暂无战队。使用 .bteam new <名称> <序号...> 创建。');
        } else {
          let out = '=== 固定战队列表 ===';
          for (const t of result.teams) {
            out += `\n  ${t.id}: ${t.name} — ${(t.members || []).join(' ')}`;
          }
          out += '\n\n在 .btast 中使用 TN 代替成员列表。';
          seal.replyToSender(ctx, msg, out);
        }
      }).catch(() => {
        const cacheRaw = ext.storageGet('pvp_teams_cache');
        if (cacheRaw) {
          try {
            const cache = JSON.parse(cacheRaw);
            const entries = Object.entries(cache);
            if (entries.length === 0) {
              seal.replyToSender(ctx, msg, '暂无战队。使用 .bteam new <名称> <序号...> 创建。');
            } else {
              let out = '=== 固定战队列表（缓存）===';
              for (const [id, info] of entries) {
                out += `\n  ${id}: ${info.name} — ${(info.members || []).join(' ')}`;
              }
              out += '\n\n在 .btast 中使用 TN 代替成员列表。\n（无法连接后端，显示的是本地缓存。）';
              seal.replyToSender(ctx, msg, out);
            }
          } catch(e) {
            seal.replyToSender(ctx, msg, '暂无战队且无法连接后端。');
          }
        } else {
          seal.replyToSender(ctx, msg, '暂无战队且无法连接后端。');
        }
      });

  } else if (sub === 'delete') {
    const teamId = (args[1] || '').toUpperCase();
    if (!teamId) {
      seal.replyToSender(ctx, msg, '用法：.bteam delete <TN>\n例：.bteam delete T1');
      return seal.ext.newCmdExecuteResult(true);
    }
    pvpFetch('/api/pvp/team/delete', {
      player_id: playerId,
      team_id: teamId,
    }).then(result => {
      if (result.error) {
        seal.replyToSender(ctx, msg, `[战队] ${result.message}`);
        return;
      }
      const cacheRaw = ext.storageGet('pvp_teams_cache');
      if (cacheRaw) {
        try {
          const cache = JSON.parse(cacheRaw);
          delete cache[teamId];
          ext.storageSet('pvp_teams_cache', JSON.stringify(cache));
        } catch(e) {}
      }
      seal.replyToSender(ctx, msg, result.message || `战队 ${teamId} 已删除。`);
    });

  } else {
    seal.replyToSender(ctx, msg, '用法：\n.bteam new <名称> <序号...>\n.bteam list\n.bteam delete <TN>');
  }
  return seal.ext.newCmdExecuteResult(true);
};
ext.cmdMap['bteam'] = cmdBteam;


// ============================================================
//  .alist  — 输出可用操作表
// ============================================================
const cmdAlist = seal.ext.newCmdItemInfo();
cmdAlist.name = 'alist';
cmdAlist.help = '.alist // 输出当前可用操作表（sN, .g, .u 等）';
cmdAlist.solve = (ctx, msg, cmdArgs) => {
  const gid = ctx.group ? ctx.group.groupId : 'private';
  const autoMode = getAutoMode(ctx);
  const battleId = ext.storageGet(`pvp_battle_${gid}`);

  if (autoMode >= 2 && battleId) {
    const playerId = ctx.player.userId;
    const baseUrl = getPvpServerUrl();
    fetch(`${baseUrl}/api/pvp/${battleId}/alist?player_id=${encodeURIComponent(playerId)}`, { method: 'GET' })
      .then(r => r.json())
      .then(result => {
        if (result.error) {
          seal.replyToSender(ctx, msg, `[.setab 2] ${result.message}`);
          return;
        }
        let out = '';
        const chars = result.characters || [];
        for (const ch of chars) {
          out += `\n【${ch.name}${ch.serial ? '(' + ch.serial + ')' : ''} HP:${ch.hp}/${ch.hp_max} MP:${ch.mp}/${ch.mp_max}】`;
          out += `\n主动作(剩余${ch.actions['主动'] || 0}):`;
          for (const sk of ch.skills) {
            if (sk.timing.includes('主') || sk.index === 0) {
              const status = sk.available ? '' : ' [暂不可用]';
              const mpStr = sk.mp_cost > 0 ? ` MP:${sk.mp_cost}` : '';
              const skillDetail = sk.index === 0 ? ` [${sk.skill_name}=${sk.skill_val}]` : '';
              out += `\n  .s${sk.index}  ${sk.name}${mpStr}${skillDetail}${status}`;
            }
          }
          out += `\n附加动作(剩余${ch.actions['附加'] || 0}):`;
          out += `\n  .a m <坐标>  — 移动`;
          for (const sk of ch.skills) {
            if (sk.timing.includes('附')) {
              const status = sk.available ? '' : ' [暂不可用]';
              const mpStr = sk.mp_cost > 0 ? ` MP:${sk.mp_cost}` : '';
              out += `\n  .a s${sk.index}  ${sk.name}${mpStr}${status}`;
            }
          }
          out += `\n其他:`;
          out += `\n  .i end  — 结束回合`;
          if (ch.has_eat) out += '\n  .a eat  — 食用治疗物品';
          if (ch.has_craft) out += '\n  .g <物品名> @目标  — 转移制造物';
          if (ch.has_items) out += '\n  .u <物品名>  — 使用物品';
          out += '\n';
        }
        if (chars.length === 0) {
          out = '你当前没有操控任何角色。请使用 .as <序号> 加入战斗。';
        }
        seal.replyToSender(ctx, msg, out);
      });

  } else {
    seal.replyToSender(ctx, msg, '当前不在 .setab 2 战斗中。请先使用 .btast 开始战斗。');
  }
  return seal.ext.newCmdExecuteResult(true);
};
ext.cmdMap['alist'] = cmdAlist;


// ============================================================
//  .clist  — 输出战斗内人物列表
// ============================================================
const cmdClist = seal.ext.newCmdItemInfo();
cmdClist.name = 'clist';
cmdClist.help = '.clist // 输出当前战斗内人物列表（含控制者信息）';
cmdClist.solve = (ctx, msg, cmdArgs) => {
  const gid = ctx.group ? ctx.group.groupId : 'private';
  const autoMode = getAutoMode(ctx);
  const battleId = ext.storageGet(`pvp_battle_${gid}`);

  if (autoMode >= 2 && battleId) {
    const baseUrl = getPvpServerUrl();
    fetch(`${baseUrl}/api/pvp/${battleId}/clist`, { method: 'GET' })
      .then(r => r.json())
      .then(result => {
        if (result.error) {
          seal.replyToSender(ctx, msg, `[.setab 2] ${result.message}`);
          return;
        }
        let out = `=== 第${result.round}回合 战斗角色列表 ===`;
        const chars = result.characters || [];
        const yChars = chars.filter(c => c.team === 'Y');
        const xChars = chars.filter(c => c.team === 'X');

        if (yChars.length > 0) {
          out += '\n[Y队]';
          for (const c of yChars) {
            const active = c.is_active ? ' ◀当前行动' : '';
            const summonTag = c.is_summon ? '(召)' : '';
            let ctrlStr = '';
            if (c.is_summon) {
              ctrlStr = c.owner_name ? `所属:${c.owner_name}` : '';
            } else if (c.controller_ids && c.controller_ids.length > 0) {
              ctrlStr = `控制:${c.controller_ids.join(',')}`;
            } else {
              ctrlStr = '控制:AI';
            }
            const serialStr = c.serial ? `(${c.serial})` : '';
            out += `\n  [${c.index}] ${c.name}${serialStr}${summonTag} HP:${c.hp}/${c.hp_max} ${ctrlStr}${active}`;
          }
        }
        if (xChars.length > 0) {
          out += '\n[X队]';
          for (const c of xChars) {
            const active = c.is_active ? ' ◀当前行动' : '';
            const summonTag = c.is_summon ? '(召)' : '';
            let ctrlStr = '';
            if (c.is_summon) {
              ctrlStr = c.owner_name ? `所属:${c.owner_name}` : '';
            } else if (c.controller_ids && c.controller_ids.length > 0) {
              ctrlStr = `控制:${c.controller_ids.join(',')}`;
            } else {
              ctrlStr = '控制:AI';
            }
            const serialStr = c.serial ? `(${c.serial})` : '';
            out += `\n  [${c.index}] ${c.name}${serialStr}${summonTag} HP:${c.hp}/${c.hp_max} ${ctrlStr}${active}`;
          }
        }
        if (chars.length === 0) {
          out += '\n（无存活角色）';
        }
        seal.replyToSender(ctx, msg, out);
      });

  } else {
    seal.replyToSender(ctx, msg, '当前不在 .setab 2 战斗中。请先使用 .btast 开始战斗。');
  }
  return seal.ext.newCmdExecuteResult(true);
};
ext.cmdMap['clist'] = cmdClist;


// ============================================================
//  .s0/.s1...  — 技能使用指令 (.setab 1 & .setab 2)
//  语法: .s2 Y12 / .s2 @张三 / .s3 A1
// ============================================================
// ============================================================
//  法术系统 — 加载、执行、效果
// ============================================================

// Chinese number mapping for spell keys (技能一 through 技能二十)
const CN_NUMS = ['零','一','二','三','四','五','六','七','八','九','十',
  '十一','十二','十三','十四','十五','十六','十七','十八','十九','二十'];

// Category letter mapping
const CAT_LETTERS = ['a','b','c','d','e','f'];

// Category type names
const CAT_NAMES = { 1:'伤害', 2:'护盾', 3:'回复', 4:'辅助', 5:'召唤', 6:'制造', 7:'引发', 0:'其他' };

// Standard auxiliary effect types (for dropdown-based parsing)
const AUX_EFFECT_TYPES = {
  1:'受到伤害+', 2:'受到伤害-',
  3:'造成伤害+', 4:'造成伤害-',
  5:'mp回复+', 6:'mp回复-',
  7:'hp回复+', 8:'hp回复-',
  9:'魔能消耗+', 10:'魔能消耗-',
  11:'致死骰优势', 12:'致死骰劣势',
  13:'伤害骰优势', 14:'伤害骰劣势',
  15:'伤害成功率加减', 16:'伤害成功率奖励惩罚',
  17:'以上全部的成倍变化'
};
const AUX_EFFECT_NAMES_TO_CODE = {};
for (const [k, v] of Object.entries(AUX_EFFECT_TYPES)) {
  AUX_EFFECT_NAMES_TO_CODE[v] = parseInt(k);
}

// Spell timing names
const TIMING_NAMES = { 1:'被动', 2:'主动作', 3:'附加动作', 4:'反应' };

// Target type names (concatenated codes: "14" = 自身+敌方单体)
const TARGET_NAMES = { 1:'自身', 2:'友方单体', 3:'友方群体', 4:'敌方单体', 5:'敌方群体',
  12:'任意己方单体', 13:'自身+友方群体', 14:'自身+敌方单体', 15:'自身+敌方群体',
  124:'任意单体', 35:'领域内全体',
  25:'友方单体+敌方群体', 45:'敌方单体+敌方群体',
  125:'任意己方单体+敌方群体' };

/** Load all spells from a character card. Returns array of spell objects. */
function loadSpells(ctx) {
  const spells = [];
  for (let i = 1; i <= 20; i++) {
    const prefix = `技能${CN_NUMS[i]}`;
    const nameV = seal.vars.strGet(ctx, `${prefix}名称`);
    if (!nameV[1] || !nameV[0]) continue;  // No spell at this slot
    const spell = { index: i, name: nameV[0], effects: [] };
    // Read flat int/str attrs
    const intKeys = ['级别','类别','消耗mp','消耗san','熟练度','里程碑','吟唱回合','默认延续性'];
    // 时机 is read as string (concatenated codes: 1=被动,2=主动作,3=附加动作,4=反应, e.g. "23")
    const timingV = seal.vars.strGet(ctx, `${prefix}时机`);
    if (timingV[1]) spell['时机'] = timingV[0];
    for (const k of intKeys) {
      const v = seal.vars.intGet(ctx, `${prefix}${k}`);
      if (v[1]) spell[k] = v[0];
    }
    const strKeys = ['简介'];
    for (const k of strKeys) {
      const v = seal.vars.strGet(ctx, `${prefix}${k}`);
      if (v[1]) spell[k] = v[0];
    }
    // Defaults
    if (spell['级别'] === undefined) spell['级别'] = 1;
    if (spell['类别'] === undefined) spell['类别'] = 0;
    if (spell['时机'] === undefined) spell['时机'] = '2';  // default: 主动作 (string "2")
    if (spell['消耗mp'] === undefined) spell['消耗mp'] = 0;
    if (spell['消耗san'] === undefined) spell['消耗san'] = 0;
    if (spell['吟唱回合'] === undefined) spell['吟唱回合'] = 0;
    if (spell['默认延续性'] === undefined) spell['默认延续性'] = 0;

    // Parse categories: each letter (a-f) maps to one effect category.
    // The "类别" field stores the primary category number (1-6).
    // Additional categories are detected by the presence of letter-suffixed keys.
    // We scan letters a through f, and include those that have at least one
    // non-empty key (客体, 伤害骰, 护盾值, etc.).
    for (let ci = 0; ci < CAT_LETTERS.length; ci++) {
      const letter = CAT_LETTERS[ci];
      // Check if this category letter has any data
      const hasData = seal.vars.intGet(ctx, `${prefix}类别${letter}客体`)[1] ||
                      seal.vars.strGet(ctx, `${prefix}类别${letter}伤害骰`)[1] ||
                      seal.vars.strGet(ctx, `${prefix}类别${letter}护盾值`)[1] ||
                      seal.vars.strGet(ctx, `${prefix}类别${letter}回复hp`)[1] ||
                      seal.vars.strGet(ctx, `${prefix}类别${letter}技能加减值`)[1] ||
                      seal.vars.intGet(ctx, `${prefix}类别${letter}召唤个数`)[1] ||
                      seal.vars.intGet(ctx, `${prefix}类别${letter}引发目标法术`)[1] ||
                      seal.vars.intGet(ctx, `${prefix}类别${letter}领域中心跟随`)[1] ||
                      seal.vars.strGet(ctx, `${prefix}类别${letter}每回合伤害骰`)[1];
      if (!hasData && ci > 0) continue;  // Only letter 'a' is mandatory
      // Determine category type (spec: 1伤害 2护盾 3回复 4辅助 5召唤 6制造 0其他)
      let catType;
      if (ci === 0) {
        catType = spell['类别'] || 1;
      } else {
        // Infer from which keys exist
        if (seal.vars.strGet(ctx, `${prefix}类别${letter}伤害骰`)[1]) catType = 1;
        else if (seal.vars.strGet(ctx, `${prefix}类别${letter}护盾值`)[1]) catType = 2;
        else if (seal.vars.strGet(ctx, `${prefix}类别${letter}回复hp`)[1] ||
                 seal.vars.strGet(ctx, `${prefix}类别${letter}回复san`)[1] ||
                 seal.vars.strGet(ctx, `${prefix}类别${letter}回复mp`)[1]) catType = 3;
        else if (seal.vars.strGet(ctx, `${prefix}类别${letter}技能加减值`)[1]) catType = 4;
        else if (seal.vars.intGet(ctx, `${prefix}类别${letter}召唤个数`)[1]) catType = 5;
        else if (seal.vars.intGet(ctx, `${prefix}类别${letter}引发目标法术`)[1]) catType = 7;
        else if (seal.vars.intGet(ctx, `${prefix}类别${letter}领域中心跟随`)[1] ||
                 (seal.vars.strGet(ctx, `${prefix}类别${letter}每回合伤害骰`)[1] &&
                  seal.vars.intGet(ctx, `${prefix}类别${letter}作用半径`)[1] > 0)) catType = 8;
        else catType = 0;
      }
      const eff = { type: catType, letter };
      const catIntKeys = ['客体','作用半径','可调节性','成功率','成功率奖惩骰',
        '可反应性','可贯穿性','致死值','致死值优劣','附加效果时长','持续回合',
        '引发目标法术','引发延迟回合','制造个数','制造花费回合数','领域中心跟随','触发HP比例'];
      for (const k of catIntKeys) {
        const v = seal.vars.intGet(ctx, `${prefix}类别${letter}${k}`);
        if (v[1]) eff[k] = v[0];
      }
      const catStrKeys = ['伤害骰','附加效果','护盾值','回复hp','回复san','回复mp',
        '技能加减值','其他辅助效果a','辅助效果值a','召唤个数','召唤物模板','制造物模板',
        '每回合伤害骰','吸血比例','属性削减'];
      for (const k of catStrKeys) {
        const v = seal.vars.strGet(ctx, `${prefix}类别${letter}${k}`);
        if (v[1]) eff[k] = v[0];
      }
      // Defaults
      if (eff['客体'] === undefined) eff['客体'] = 4;  // default: enemy single
      if (eff['作用半径'] === undefined) eff['作用半径'] = 0;
      if (eff['可反应性'] === undefined) eff['可反应性'] = (catType === 1) ? 1 : 0;
      if (eff['可贯穿性'] === undefined) eff['可贯穿性'] = (catType === 1) ? 1 : 0;
      if (eff['成功率'] === undefined) eff['成功率'] = 0;
      spell.effects.push(eff);
    }
    spells.push(spell);
  }
  return spells;
}

/** Execute a damage effect against a target. Returns { dmgValue, outText }.
 *  Supports DOT (持续回合>0 stores recurring damage), lifesteal (吸血),
 *  and knockback/stun (附加效果). */
function execDamageEffect(ctx, mctx, spell, eff, gid) {
  let out = '';
  const dmgDice = eff['伤害骰'] || '1d4';
  const pen = eff['可贯穿性'] || 0;
  const leth = eff['致死值'] || 0;
  const successRate = eff['成功率'] || 0;
  const canReact = eff['可反应性'] || 0;
  const dur = eff['持续回合'] || 0;
  const lifestealRatio = eff['吸血比例'] ? parseFloat(eff['吸血比例']) : 0;
  const dotDice = eff['每回合伤害骰'] || '';  // DOT: damage per round

  // If spell has success rate, roll check
  if (successRate > 0) {
    const checkRoll = rollDice(ctx, '1d100');
    if (checkRoll > successRate) {
      return { dmgValue: 0, outText: `成功率检定: ${checkRoll}/${successRate} 失败！法术未命中。\n` };
    }
    out += `成功率检定: ${checkRoll}/${successRate} 成功。\n`;
  }

  // If target can react and is a player, prompt for reaction
  if (canReact && mctx && mctx.player) {
    // Store pending spell attack for reaction
    const attackerName = seal.format(ctx, '{$t玩家}');
    const targetName = seal.format(mctx, '{$t玩家}');
    const pKey = `pending_spell_${gid}_${mctx.player.userId}`;
    ext.storageSet(pKey, JSON.stringify({
      type: 'spell',
      attackerName, attackerUserId: ctx.player.userId,
      spellName: spell.name, spellIndex: spell.index,
      dmgDice: normalizeDice(dmgDice), pen, leth,
      targetName, targetUserId: mctx.player.userId, groupId: gid,
      atkResults: [{ roll: 1, rank: 4, detail: '' }], atkCount: 1,
      attackerRestimMode: 0, ts: Date.now(),
      lifestealRatio, dotDice, dur
    }));
    // Track for cleanup
    const tgtKey = `pending_targets_${gid}`;
    const tgtRaw = ext.storageGet(tgtKey);
    let tids = [];
    if (tgtRaw) { try { tids = JSON.parse(tgtRaw); } catch(e) {} }
    if (!tids.includes(mctx.player.userId)) { tids.push(mctx.player.userId); ext.storageSet(tgtKey, JSON.stringify(tids)); }

    out += `@${targetName} 请做出反应：\n.e 闪避 / .e d（法术伤害可反应）\n`;
    return { dmgValue: -1, outText: out, pendingReaction: true };
  }

  // Direct damage: calculate and apply
  const dmgVal = calcDamageAndSyncCard(mctx, ctx, mctx.player.userId, ctx.player.userId, gid,
    1, 1, normalizeDice(dmgDice), pen, leth, false, false);
  out += `造成 ${dmgVal} 点伤害。\n`;

  // Lifesteal: heal attacker based on damage dealt
  if (lifestealRatio > 0 && dmgVal > 0) {
    const healAmt = Math.floor(dmgVal * lifestealRatio);
    const hpStore = getCombatHP(gid);
    const atkHP = hpStore[ctx.player.userId];
    if (atkHP !== undefined) {
      const maxHP = getAttr(ctx, '体力上限', atkHP);
      hpStore[ctx.player.userId] = Math.min(atkHP + healAmt, maxHP);
      setCombatHP(gid, hpStore);
      out += `吸血回复 ${healAmt} 点HP。\n`;
    }
  }

  // DOT: store recurring damage effect
  if (dur > 0 || dotDice) {
    const effKey = `combat_effects_${gid}`;
    let effects = [];
    const raw = ext.storageGet(effKey);
    if (raw) { try { effects = JSON.parse(raw); } catch(e) {} }
    const dotTargetId = mctx ? mctx.player.userId : ctx.player.userId;
    effects.push({
      type: 'dot', remainingRounds: Math.max(dur, 1),
      dotDice: dotDice || dmgDice, pen, leth,
      sourceUserId: ctx.player.userId, targetUserId: dotTargetId,
      spellName: spell.name, spellIndex: spell.index,
      auxType: eff['附加效果'] || ''
    });
    ext.storageSet(effKey, JSON.stringify(effects));
    if (dotDice) out += `持续伤害: 每回合 ${dotDice}${Math.max(dur, 1) >= 99 ? '' : '（持续 ' + Math.max(dur, 1) + ' 回合）'}\n`;
  }

  if (leth && avgDamage(normalizeDice(dmgDice)) > 6) {
    const hpStore = getCombatHP(gid);
    const curHP = hpStore[mctx.player.userId];
    if (curHP !== undefined && curHP <= 0) {
      out += '目标生命值归零，退出战斗！\n';
    }
  }
  // Apply knockback/stun
  if (eff['附加效果']) {
    out += `附加效果: ${eff['附加效果']}（持续 ${eff['附加效果时长']||0} 回合）\n`;
  }
  return { dmgValue: dmgVal, outText: out };
}

/** Execute a shield effect. Returns outText. */
function execShieldEffect(ctx, mctx, spell, eff, gid) {
  const shieldVal = rollDice(ctx, eff['护盾值'] || '1d4');
  const dur = eff['持续回合'] || 1;
  // Store shield in combat effects
  const effKey = `combat_effects_${gid}`;
  let effects = [];
  const raw = ext.storageGet(effKey);
  if (raw) { try { effects = JSON.parse(raw); } catch(e) {} }
  const targetUserId = mctx ? mctx.player.userId : ctx.player.userId;
  effects.push({
    type: 'shield', value: shieldVal, remainingRounds: dur,
    sourceUserId: ctx.player.userId, targetUserId, spellName: spell.name,
    spellIndex: spell.index, persistent: spell['默认延续性'] || 0
  });
  ext.storageSet(effKey, JSON.stringify(effects));
  const durText = dur >= 99 ? '' : `（持续 ${dur} 回合）`;
  return `获得 ${shieldVal} 点护盾${durText}。\n`;
}

/** Execute a heal effect. Returns outText. */
function execHealEffect(ctx, mctx, spell, eff, gid) {
  let out = '';
  const hpHeal = eff['回复hp'] ? rollDice(ctx, eff['回复hp']) : 0;
  const sanHeal = eff['回复san'] ? rollDice(ctx, eff['回复san']) : 0;
  const mpHeal = eff['回复mp'] ? rollDice(ctx, eff['回复mp']) : 0;
  const targetCtx = mctx || ctx;
  if (hpHeal > 0) {
    const hpStore = getCombatHP(gid);
    const curHP = hpStore[targetCtx.player.userId] || getAttr(targetCtx, '体力', 10);
    const maxHP = getAttr(targetCtx, '体力上限', curHP);
    const newHP = Math.min(curHP + hpHeal, maxHP);
    hpStore[targetCtx.player.userId] = newHP;
    setCombatHP(gid, hpStore);
    out += `回复 HP +${hpHeal}（${curHP}→${newHP}）\n`;
  }
  if (sanHeal > 0) {
    const curSAN = getAttr(targetCtx, '理智', 50);
    const newSAN = Math.min(curSAN + sanHeal, 99);
    seal.vars.intSet(targetCtx, '理智', newSAN);
    out += `回复 SAN +${sanHeal}（${curSAN}→${newSAN}）\n`;
  }
  if (mpHeal > 0) {
    const curMP = getAttr(targetCtx, '魔力', 10);
    const maxMP = getAttr(targetCtx, '魔力上限', curMP);
    const newMP = Math.min(curMP + mpHeal, maxMP);
    seal.vars.intSet(targetCtx, '魔力', newMP);
    out += `回复 MP +${mpHeal}（${curMP}→${newMP}）\n`;
  }
  return out;
}

/** Execute a buff/debuff effect. Returns outText. */
function execBuffEffect(ctx, mctx, spell, eff, gid) {
  const dur = eff['持续回合'] || 1;
  const effKey = `combat_effects_${gid}`;
  let effects = [];
  const raw = ext.storageGet(effKey);
  if (raw) { try { effects = JSON.parse(raw); } catch(e) {} }
  const targetUserId = mctx ? mctx.player.userId : ctx.player.userId;
  const auxType = eff['其他辅助效果a'] || '';
  effects.push({
    type: 'buff', remainingRounds: dur,
    skillMod: eff['技能加减值'] || '',
    auxType: auxType, auxVal: eff['辅助效果值a'] || '',
    auxCode: AUX_EFFECT_NAMES_TO_CODE[auxType] || 0,
    sourceUserId: ctx.player.userId, targetUserId, spellName: spell.name,
    spellIndex: spell.index, persistent: spell['默认延续性'] || 0
  });
  ext.storageSet(effKey, JSON.stringify(effects));
  const durText3 = dur >= 99 ? '' : `（持续 ${dur} 回合）`;
  return `施加辅助效果${durText3}：${eff['技能加减值']||''} ${auxType}${eff['辅助效果值a'] ? '('+eff['辅助效果值a']+')' : ''}\n`;
}

/** Execute a trigger/chain effect: after N delayed rounds, automatically
 *  cast the target spell. If source spell is persistent, it ends when trigger fires
 *  (representing a spell transforming form). Returns outText. */
function execTriggerEffect(ctx, mctx, spell, eff, gid) {
  const targetSpellIndex = eff['引发目标法术'] || 0;
  const delayRounds = eff['引发延迟回合'] || 0;
  if (!targetSpellIndex) return '引发类法术未指定目标法术！\n';

  const effKey = `combat_effects_${gid}`;
  let effects = [];
  const raw = ext.storageGet(effKey);
  if (raw) { try { effects = JSON.parse(raw); } catch(e) {} }
  effects.push({
    type: 'trigger',
    remainingRounds: delayRounds,
    triggerSpellIndex: targetSpellIndex,
    sourceSpellIndex: spell.index,
    sourceSpellName: spell.name,
    sourceUserId: ctx.player.userId,
    spellName: spell.name,
    spellIndex: spell.index,
    persistent: spell['默认延续性'] || 0
  });
  ext.storageSet(effKey, JSON.stringify(effects));
  return `引发法术已设置：${delayRounds} 回合后将自动释放技能${targetSpellIndex}。\n`;
}

/** Execute a zone/area effect: creates a persistent area on the map that
 *  applies per-round effects (damage/heal/buff) to all targets within radius.
 *  If 领域中心跟随=1, the zone center moves with the caster each round. */
function execZoneEffect(ctx, mctx, spell, eff, gid) {
  const radius = eff['作用半径'] || 0;
  const dur = eff['持续回合'] || 1;
  const tickDmg = eff['每回合伤害骰'] || '';
  const tickHealHp = eff['回复hp'] || '';
  const tickHealMp = eff['回复mp'] || '';
  const tickHealSan = eff['回复san'] || '';
  const centerFollows = eff['领域中心跟随'] || 0;
  const filter = eff['客体'] || 35;  // default: all in area
  const attributeDebuff = eff['属性削减'] || '';

  // Find current caster position
  let centerCoord = '';
  const mapRaw = ext.storageGet(`combat_map_${gid}`);
  if (mapRaw) {
    try {
      const md = JSON.parse(mapRaw);
      if (md.occupants) {
        for (const [c, occ] of Object.entries(md.occupants)) {
          if (occ === ctx.player.userId || occ === seal.format(ctx, '{$t玩家}')) {
            centerCoord = c; break;
          }
        }
      }
    } catch(e) {}
  }

  // If mctx is specified and uses a coordinate (e.g. .s3 A1), use that
  const args = [];  // coordinate could be passed differently — for now default to caster position
  if (!centerCoord) centerCoord = 'A1';  // fallback

  const effKey = `combat_effects_${gid}`;
  let effects = [];
  const raw = ext.storageGet(effKey);
  if (raw) { try { effects = JSON.parse(raw); } catch(e) {} }
  effects.push({
    type: 'zone', center: centerCoord, radius, remainingRounds: dur,
    tickDmg, tickHealHp, tickHealMp, tickHealSan,
    centerFollows, filter, attributeDebuff,
    sourceUserId: ctx.player.userId, spellName: spell.name,
    spellIndex: spell.index, persistent: spell['默认延续性'] || 0
  });
  ext.storageSet(effKey, JSON.stringify(effects));
  const durText4 = dur >= 99 ? '（永久）' : `（持续${dur}回合）`;
  let out = `创建领域【${spell.name}】（半径${radius}格${durText4}，中心${centerCoord}）。\n`;
  if (tickDmg) out += `每回合伤害: ${tickDmg}\n`;
  if (tickHealHp) out += `每回合治疗: HP ${tickHealHp}\n`;
  if (tickHealMp) out += `每回合回复: MP ${tickHealMp}\n`;
  if (attributeDebuff) out += `属性削减: ${attributeDebuff}\n`;
  return out;
}

/** Main spell execution dispatch. Returns output text. */
function executeSpell(ctx, mctx, spell, gid, actionType) {
  let out = '';
  const pn = seal.format(ctx, '{$t玩家}');
  const targetName = mctx ? seal.format(mctx, '{$t玩家}') : '自身';
  out += `${pn} 释放【${spell.name}】→ ${targetName}\n`;

  // Check and deduct MP/SAN
  const mpCost = spell['消耗mp'] || 0;
  const sanCost = spell['消耗san'] || 0;
  if (mpCost > 0) {
    const curMP = getAttr(ctx, '魔力', 0) || getAttr(ctx, 'mp', 0);
    if (curMP < mpCost) {
      return `${pn} 魔力不足！需要 ${mpCost} MP，当前 ${curMP} MP。`;
    }
    seal.vars.intSet(ctx, '魔力', Math.max(0, curMP - mpCost));
    out += `消耗 ${mpCost} MP`;
  }
  if (sanCost > 0) {
    const curSAN = getAttr(ctx, '理智', 50);
    seal.vars.intSet(ctx, '理智', Math.max(0, curSAN - sanCost));
    out += `${mpCost > 0 ? '，' : '消耗 '}${sanCost} SAN`;
  }
  if (mpCost > 0 || sanCost > 0) out += '\n';

  // Execute each effect (type = spec category number: 1伤害 2护盾 3回复 4辅助 5召唤 6制造)
  let pendingReaction = false;
  for (const eff of spell.effects) {
    switch (eff.type) {
      case 1: {  // 伤害
        const result = execDamageEffect(ctx, mctx, spell, eff, gid);
        out += result.outText;
        if (result.pendingReaction) pendingReaction = true;
        break;
      }
      case 2: {  // 护盾
        out += execShieldEffect(ctx, mctx, spell, eff, gid);
        break;
      }
      case 3: {  // 回复
        out += execHealEffect(ctx, mctx, spell, eff, gid);
        break;
      }
      case 4: {  // 辅助
        out += execBuffEffect(ctx, mctx, spell, eff, gid);
        break;
      }
      case 5: {  // 召唤
        const summonCount = eff['召唤个数'] || 1;
        const summonDur = eff['持续回合'] || 1;
        const summonTemplateName = eff['召唤物模板'] || '';
        const effKey2 = `combat_effects_${gid}`;
        let effects2 = [];
        const raw2 = ext.storageGet(effKey2);
        if (raw2) { try { effects2 = JSON.parse(raw2); } catch(e) {} }

        // Load summon template stats
        const summonTmpls = getCachedSummonTemplates(ctx, gid);
        const tmpl = summonTmpls.find(t => t.name === summonTemplateName);
        const stats = tmpl ? getSummonStats(tmpl) : { hp: 10, dex: 50, dodge: 25, mov: 6, actions: 1, canCounter: 1, canReact: 1, skills: [] };

        // Find caster position on map
        let casterCoord = null;
        const mapRaw5 = ext.storageGet(`combat_map_${gid}`);
        if (mapRaw5) {
          try {
            const md5 = JSON.parse(mapRaw5);
            if (md5.occupants) {
              for (const [c, occ] of Object.entries(md5.occupants)) {
                if (occ === ctx.player.userId || occ === seal.format(ctx, '{$t玩家}')) {
                  casterCoord = c; break;
                }
              }
            }
          } catch(e) {}
        }

        // Add summons to initiative
        const initList = getInitiative(gid);
        for (let sc = 0; sc < summonCount; sc++) {
          // Summon ID: {caster_serial}_sum_{n} (e.g. Y12_sum_1)
          const casterSerial = getCharSerial(ctx) || ctx.player.userId;
          const summonKey = `summon_counter_${gid}_${casterSerial}`;
          let summonN = (parseInt(ext.storageGet(summonKey)) || 0) + 1;
          ext.storageSet(summonKey, String(summonN));
          const summonId = `${casterSerial}_sum_${summonN}`;
          const summonName = `${summonTemplateName || spell.name}_${sc + 1}`;
          const summonDisplay = `${ctx.player.nickname || '?'} 的 ${summonTemplateName || spell.name}`;
          effects2.push({
            type: 'summon', summonId, template: summonTemplateName,
            remainingRounds: summonDur,
            sourceUserId: ctx.player.userId, spellName: spell.name,
            spellIndex: spell.index, persistent: spell['默认延续性'] || 0,
            summonName, hp: stats.hp, maxHp: stats.maxHp,
            mp: stats.mp, maxMp: stats.maxMp,
            dex: stats.dex, dodge: stats.dodge, mov: stats.mov,
            actions: stats.actions, canCounter: stats.canCounter,
            canReact: stats.canReact, skills: stats.skills
          });

          // Add to initiative (summon acts on summoner's team)
          const summonerEntry = initList.find(e => e.userId === ctx.player.userId);
          const team = summonerEntry ? summonerEntry.team : 'Y';
          const sumInitRoll = rollDice(mctx, '1d100');
          const sumInitRank = successRank(sumInitRoll, stats.dex);
          initList.push({
            userId: summonId, name: summonTemplateName, displayName: summonDisplay, team,
            dex: stats.dex, initRoll: sumInitRoll, initRank: sumInitRank,
            coord: '', isSummon: true, ownerUserId: ctx.player.userId
          });

          // Place summon on map near caster
          if (mapRaw5) {
            try {
              const mapData2 = JSON.parse(mapRaw5);
              if (!mapData2.occupants) mapData2.occupants = {};
              if (casterCoord) {
                const cp = parseCoord(casterCoord);
                if (cp) {
                  let placed = false;
                  // Search in expanding radius for empty cell
                  for (let radius = 1; radius <= 5 && !placed; radius++) {
                    for (let dr = -radius; dr <= radius && !placed; dr++) {
                      for (let dc = -radius; dc <= radius && !placed; dc++) {
                        if (Math.abs(dr) + Math.abs(dc) !== radius) continue;
                        const coord = formatCoord(
                          Math.max(0, Math.min(mapData2.width - 1, cp.col + dc)),
                          Math.max(0, Math.min(mapData2.height - 1, cp.row + dr)));
                        if (!mapData2.occupants[coord] && !(mapData2.obstacles && mapData2.obstacles[coord])) {
                          mapData2.occupants[coord] = summonId;
                          // Update initList entry with coord
                          const siEntry = initList.find(e => e.userId === summonId);
                          if (siEntry) siEntry.coord = coord;
                          placed = true;
                        }
                      }
                    }
                  }
                }
              }
              ext.storageSet(`combat_map_${gid}`, JSON.stringify(mapData2));
            } catch(e) {}
          }
        }
        // Sort initiative (higher initRoll first)
        initList.sort((a, b) => b.initRoll - a.initRoll || b.dex - a.dex);
        setInitiative(gid, initList);
        ext.storageSet(effKey2, JSON.stringify(effects2));
        const sDurText = summonDur >= 99 ? '' : ` 持续 ${summonDur} 回合`;
        out += `召唤 ${summonCount} 个【${summonTemplateName || '使魔'}】（HP${stats.hp} DEX${stats.dex}${sDurText}）。\n`;
        break;
      }
      case 6: {  // 制造
        const createCount = parseInt(eff['制造个数']) || 1;
        const craftRounds = parseInt(eff['制造花费回合数']) || 0;
        const createDur = eff['持续回合'] || 0;
        const craftTemplate = eff['制造物模板'] || '';
        const effKey3 = `combat_effects_${gid}`;
        let effects3 = [];
        const raw3 = ext.storageGet(effKey3);
        if (raw3) { try { effects3 = JSON.parse(raw3); } catch(e) {} }
        for (let cc = 0; cc < createCount; cc++) {
          const craftId = `craft_${spell.index}_${cc}_${Date.now()}`;
          effects3.push({
            type: 'create', craftId, template: craftTemplate,
            remainingRounds: createDur,
            craftRoundsRemaining: craftRounds,
            totalCraftRounds: craftRounds,
            sourceUserId: ctx.player.userId,
            targetUserId: mctx ? mctx.player.userId : ctx.player.userId,
            spellName: spell.name, spellIndex: spell.index,
            persistent: spell['默认延续性'] || 0
          });
        }
        ext.storageSet(effKey3, JSON.stringify(effects3));
        out += `开始制造 ${createCount} 个【${craftTemplate || '物品'}】`;
        if (craftRounds > 0) out += `（需要 ${craftRounds} 回合）`;
        out += `\n`;
        break;
      }
      case 7: {  // 引发
        out += execTriggerEffect(ctx, mctx, spell, eff, gid);
        break;
      }
      case 8: {  // 领域/区域效果
        out += execZoneEffect(ctx, mctx, spell, eff, gid);
        break;
      }
      default: {
        out += `类别${eff.letter}: 暂不支持的效果类型(${eff.type})。\n`;
        break;
      }
    }
  }

  // Track spell usage for proficiency
  const profV = seal.vars.intGet(ctx, `技能${CN_NUMS[spell.index]}熟练度`);
  const curProf = profV[1] ? profV[0] : 0;
  seal.vars.intSet(ctx, `技能${CN_NUMS[spell.index]}熟练度`, curProf);

  return out;
}

/** Look up a character's spells and cache them for the combat session */
function getCachedSpells(ctx, gid) {
  const cacheKey = `combat_spells_${gid}_${ctx.player.userId}`;
  const raw = ext.storageGet(cacheKey);
  if (raw) {
    try { return JSON.parse(raw); } catch(e) {}
  }
  const spells = loadSpells(ctx);
  ext.storageSet(cacheKey, JSON.stringify(spells));
  return spells;
}

/** Load craft/created-item templates from character card (same structure as summons).
 *  Reads 制造物模板1 through 制造物模板N keys: HP, MP, SAN, base attrs, skills. */
function loadCraftTemplates(ctx) {
  const templates = [];
  for (let i = 1; i <= 10; i++) {
    const prefix = `制造物模板${CN_NUMS[i] || i}`;
    const nameV = seal.vars.strGet(ctx, `${prefix}名称`);
    // Also try bare key for first template
    let name = '';
    if (nameV[1]) name = nameV[0];
    else if (i === 1) {
      const bareV = seal.vars.strGet(ctx, '制造物模板');
      if (bareV[1]) name = bareV[0];
    }
    if (!name) continue;
    const tmpl = { index: i, name };
    const baseKeys = ['HP','MP','SAN','STR','CON','SIZ','DEX','APP','INT','POW','EDU'];
    for (const k of baseKeys) {
      const v = seal.vars.intGet(ctx, `${prefix}${k}`);
      if (v[1]) tmpl[k] = v[0];
      // Also try abbreviated key pattern
      if (tmpl[k] === undefined) {
        const v2 = seal.vars.intGet(ctx, `制造物模板${i}${k}`);
        if (v2[1]) tmpl[k] = v2[0];
      }
    }
    // Read skills (up to 3)
    tmpl.skills = [];
    for (let si = 1; si <= 3; si++) {
      const skV = seal.vars.strGet(ctx, `${prefix}技能${si}`);
      if (skV[1]) tmpl.skills.push(skV[0]);
      else {
        const skV2 = seal.vars.strGet(ctx, `制造物模板${i}技能${si}`);
        if (skV2[1]) tmpl.skills.push(skV2[0]);
      }
    }
    templates.push(tmpl);
  }
  return templates;
}

/** Look up craft templates and cache them for the combat session */
function getCachedCraftTemplates(ctx, gid) {
  const cacheKey = `combat_crafts_${gid}_${ctx.player.userId}`;
  const raw = ext.storageGet(cacheKey);
  if (raw) {
    try { return JSON.parse(raw); } catch(e) {}
  }
  const tmpls = loadCraftTemplates(ctx);
  ext.storageSet(cacheKey, JSON.stringify(tmpls));
  return tmpls;
}

/** Load summon templates from character card (same structure as craft templates).
 *  Reads 召唤物模板1 through 召唤物模板N keys: HP, MP, SAN, base attrs, skills,
 *  闪避, MOV, 行动次数, 可反击, 可反应. */
function loadSummonTemplates(ctx) {
  const templates = [];
  for (let i = 1; i <= 10; i++) {
    const prefix = `召唤物模板${CN_NUMS[i] || i}`;
    const nameV = seal.vars.strGet(ctx, `${prefix}名称`);
    let name = '';
    if (nameV[1]) name = nameV[0];
    else if (i === 1) {
      const bareV = seal.vars.strGet(ctx, '召唤物模板');
      if (bareV[1]) name = bareV[0];
    }
    if (!name) continue;
    const tmpl = { index: i, name };
    const baseKeys = ['HP','MP','SAN','STR','CON','SIZ','DEX','APP','INT','POW','EDU',
                      '闪避','MOV','行动次数','可反击','可反应'];
    for (const k of baseKeys) {
      const v = seal.vars.intGet(ctx, `${prefix}${k}`);
      if (v[1]) tmpl[k] = v[0];
      if (tmpl[k] === undefined) {
        const v2 = seal.vars.intGet(ctx, `召唤物模板${i}${k}`);
        if (v2[1]) tmpl[k] = v2[0];
      }
    }
    // Defaults
    if (tmpl['闪避'] === undefined) tmpl['闪避'] = Math.floor((tmpl['DEX'] || 50) / 2);
    if (tmpl['MOV'] === undefined) tmpl['MOV'] = 6;
    if (tmpl['行动次数'] === undefined) tmpl['行动次数'] = 1;
    if (tmpl['可反击'] === undefined) tmpl['可反击'] = 1;
    if (tmpl['可反应'] === undefined) tmpl['可反应'] = 1;
    // Read skills (up to 3)
    tmpl.skills = [];
    for (let si = 1; si <= 3; si++) {
      const skV = seal.vars.strGet(ctx, `${prefix}技能${si}`);
      if (skV[1]) tmpl.skills.push(skV[0]);
      else {
        const skV2 = seal.vars.strGet(ctx, `召唤物模板${i}技能${si}`);
        if (skV2[1]) tmpl.skills.push(skV2[0]);
      }
    }
    templates.push(tmpl);
  }
  return templates;
}

/** Look up summon templates and cache them for the combat session */
function getCachedSummonTemplates(ctx, gid) {
  const cacheKey = `combat_summons_${gid}_${ctx.player.userId}`;
  const raw = ext.storageGet(cacheKey);
  if (raw) {
    try { return JSON.parse(raw); } catch(e) {}
  }
  const tmpls = loadSummonTemplates(ctx);
  ext.storageSet(cacheKey, JSON.stringify(tmpls));
  return tmpls;
}

/** Apply a summon's combat stats to the target context (for when summon acts).
 *  Returns the summon stat block for combat resolution. */
function getSummonStats(tmpl) {
  return {
    hp: tmpl.HP || 10, maxHp: tmpl.HP || 10,
    mp: tmpl.MP || 0, maxMp: tmpl.MP || 0,
    san: tmpl.SAN || 50,
    dex: tmpl.DEX || 50,
    mov: tmpl.MOV || 6,
    dodge: tmpl['闪避'] || Math.floor((tmpl.DEX || 50) / 2),
    actions: tmpl['行动次数'] || 1,
    canCounter: tmpl['可反击'] || 0,
    canReact: tmpl['可反应'] || 1,
    skills: tmpl.skills || [],
    str: tmpl.STR || 50, con: tmpl.CON || 50, siz: tmpl.SIZ || 50,
    app: tmpl.APP || 50, int: tmpl.INT || 50, pow: tmpl.POW || 50, edu: tmpl.EDU || 50
  };
}

/** End persistent effects for a given spell index. Called by .sN end command. */
function endPersistentSpell(ctx, msg, gid, skillNum) {
  const effKey = `combat_effects_${gid}`;
  const raw = ext.storageGet(effKey);
  if (!raw) {
    seal.replyToSender(ctx, msg, '没有活跃的战斗效果。');
    return seal.ext.newCmdExecuteResult(true);
  }
  let effects;
  try { effects = JSON.parse(raw); } catch(e) {
    seal.replyToSender(ctx, msg, '效果数据损坏。');
    return seal.ext.newCmdExecuteResult(true);
  }
  const removed = [];
  effects = effects.filter(eff => {
    if (eff.spellIndex === skillNum && (eff.persistent || eff.persistentActive)) {
      removed.push(eff.spellName || `技能${skillNum}`);
      // Remove summon/craft from map if applicable
      if ((eff.type === 'summon' && eff.summonId) || (eff.type === 'create' && eff.craftId)) {
        const entId = eff.summonId || eff.craftId;
        const mapRaw = ext.storageGet(`combat_map_${gid}`);
        if (mapRaw) {
          try {
            const md = JSON.parse(mapRaw);
            if (md.occupants) {
              for (const [coord, occ] of Object.entries(md.occupants)) {
                if (occ === entId) { delete md.occupants[coord]; break; }
              }
              ext.storageSet(`combat_map_${gid}`, JSON.stringify(md));
            }
          } catch(e) {}
        }
      }
      return false;
    }
    return true;
  });
  ext.storageSet(effKey, effects.length > 0 ? JSON.stringify(effects) : '');
  const pn = seal.format(ctx, '{$t玩家}');
  if (removed.length > 0) {
    seal.replyToSender(ctx, msg, `${pn} 结束了持续效果: ${removed.join(', ')}`);
  } else {
    seal.replyToSender(ctx, msg, `未找到技能${skillNum}的活跃持续效果。`);
  }
  return seal.ext.newCmdExecuteResult(true);
}

function makeSkillCmd(skillNum) {
  const cmd = seal.ext.newCmdItemInfo();
  cmd.name = `s${skillNum}`;
  if (skillNum === 0) {
    cmd.help = '.s0 // 基础近战攻击（使用最高近战技能，等同于空 .bta）';
  } else {
    cmd.help = `.s${skillNum} <目标> // 释放技能${skillNum}\n` +
      `.s${skillNum} Y12  // 目标为Y12\n` +
      `.s${skillNum} @某人  // 目标为@某人绑定的魔法少女序号`;
  }
  cmd.allowDelegate = true;
  cmd.solve = (ctx, msg, cmdArgs) => {
    const gid = ctx.group ? ctx.group.groupId : 'private';

    // --- .setab 2 HTTP path (must be checked first — backend manages state) ---
    const _autoMode = getAutoMode(ctx);
    const _battleId = ext.storageGet(`pvp_battle_${gid}`);
    if (_autoMode >= 2 && _battleId) {
      const _mctx = seal.getCtxProxyFirst(ctx, cmdArgs);
      let _targetId = '';
      if (_mctx && _mctx.player && _mctx.player.userId !== ctx.player.userId) {
        _targetId = _mctx.player.userId;
      }
      pvpFetch(`/api/pvp/${_battleId}/action`, {
        player_id: ctx.player.userId,
        action: `.s${skillNum}`,
        target: _targetId,
        args: cmdArgs.cleanArgs,
      }).then(_result => {
        if (_result.error) {
          seal.replyToSender(ctx, msg, `[.setab 2] ${_result.message}`);
          return;
        }
        applyServerChanges(gid, _result);
        let _out = _result.output || '';
        if (_result.needs_reaction && _result.pending_attack) {
          ext.storageSet(`pvp_pending_${gid}`, JSON.stringify(_result.pending_attack));
        }
        if (_result.auto_turns && _result.auto_turns.length > 0) {
          const nodes = _result.auto_turns.map((t, i) => {
            const label = (_result.turn_labels && _result.turn_labels[i]) || `行动 ${i+1}`;
            return `【${label}】\n${t}`;
          });
          sendForwardMessage(ctx, msg, nodes, 'AI 自动战斗');
        }
        if (_result.state && _result.state.phase !== 'active') {
          ext.storageSet(`pvp_battle_${gid}`, '');
          _out += '\n\n=== 战斗结束 ===';
        }
        seal.replyToSender(ctx, msg, _out);
      });
      return seal.ext.newCmdExecuteResult(true);
    }

    // --- .setab 0/1: local state must be active ---
    const state = getCombatState(gid);
    if (!state || state.phase !== 'active') {
      seal.replyToSender(ctx, msg, '当前不在战斗中！请先使用 .bta start 开始战斗。');
      return seal.ext.newCmdExecuteResult(true);
    }

    // --- .sN end — manually end persistent effects (.setab 0/1 only) ---
    const endArgs = cmdArgs.cleanArgs.split(/\s+/).filter(a => a.length > 0);
    if (endArgs.length >= 1 && endArgs[0].toLowerCase() === 'end') {
      return endPersistentSpell(ctx, msg, gid, skillNum);
    }

    // Verify it's this player's turn
    const initList = getInitiative(gid);
    const actions = getActions(gid);
    const userId = ctx.player.userId;
    const activeEntry = initList[state.activeIndex];
    if (!activeEntry || activeEntry.userId !== userId) {
      seal.replyToSender(ctx, msg, `现在不是你的回合！当前行动: ${activeEntry ? activeEntry.name : '?'}`);
      return seal.ext.newCmdExecuteResult(true);
    }
    // Check remaining actions
    const myActions = actions[userId] || { 附加: 0, 主动: 0 };
    if (myActions.主动 <= 0) {
      seal.replyToSender(ctx, msg, '你的主动作次数已用尽！使用 .a sN 消耗附加动作，或等待下一回合。');
      return seal.ext.newCmdExecuteResult(true);
    }

    let output = '';
    let isPassive = false;  // scoped for use in action decrement
    const mctx = seal.getCtxProxyFirst(ctx, cmdArgs);

    if (skillNum === 0) {
      // Basic melee attack — use best melee skill, go through standard .bta flow
      const best = getBestMeleeSkill(ctx);
      const pn = seal.format(ctx, '{$t玩家}');
      if (mctx) {
        // Execute as a melee attack with auto hit roll
        const targetName = seal.format(mctx, '{$t玩家}');
        initCombatHP(gid, userId, ctx, mctx.player.userId, mctx);

        // --- Melee range check: 5x5 square centered on attacker ---
        const mapRaw5 = ext.storageGet(`combat_map_${gid}`);
        if (mapRaw5) {
          try {
            const md5 = JSON.parse(mapRaw5);
            if (md5.occupants) {
              let atkCoord = '', tgtCoord = '';
              for (const [c, occ] of Object.entries(md5.occupants)) {
                if (occ === userId || occ === pn) atkCoord = c;
                if (occ === mctx.player.userId || occ === targetName) tgtCoord = c;
              }
              if (atkCoord && tgtCoord && !isInMeleeRange(atkCoord, tgtCoord)) {
                seal.replyToSender(ctx, msg,
                  `目标 ${targetName} 不在近战范围内！（近战范围：以自身为中心的5×5正方形）\n` +
                  `你的位置: ${atkCoord}  目标位置: ${tgtCoord}`);
                return seal.ext.newCmdExecuteResult(true);
              }
            }
          } catch(e) {}
        }

        const atkRollInfo = rollD100(ctx, '');
        const atkRank = successRank(atkRollInfo.result, best.value);
        const bpD = atkRollInfo.detail ? `, ${atkRollInfo.detail}` : '';

        output = `${pn} 使用基础近战攻击【${best.name}=${best.value}】→ ${targetName}\n`;
        output += `D100=${atkRollInfo.result}/${best.value}` +
          (bpD ? ` (${atkRollInfo.result}[D100=${atkRollInfo.result}${bpD}])` : '') +
          ` ${rankText(ctx, atkRank)}\n`;

        if (isRangedSkill(best.name)) {
          // Shouldn't happen for melee, but handle
          if (atkRank > 0) {
            const dmgVal = calcDamageAndSyncCard(mctx, ctx, mctx.player.userId, userId, gid,
              atkRank, atkRollInfo.result, '1d4', 1, 0, false, false);
            output += `直接命中，造成 ${dmgVal} 点伤害。\n`;
          } else {
            output += '未命中！\n';
          }
        } else {
          // Melee: store pending for reaction
          const pKey = `pending_atk_${gid}_${mctx.player.userId}`;
          initCombatHP(gid, userId, ctx, mctx.player.userId, mctx);
          ext.storageSet(pKey, JSON.stringify({
            attackerName: pn, attackerUserId: userId,
            skillName: best.name, skillValue: best.value,
            atkResults: [{ roll: atkRollInfo.result, rank: atkRank, detail: atkRollInfo.detail }], atkCount: 1,
            damageDice: '1d4', penetration: 1, lethality: 0,
            bpSuffix: '', dmgAdvantage: false, dmgDisadvantage: false,
            targetName, targetUserId: mctx.player.userId,
            groupId: gid, subTarget: '', atkResultLine: output,
            attackerRestimMode: 0, ts: Date.now()
          }));
          const tgtKey = `pending_targets_${gid}`;
          const tgtRaw = ext.storageGet(tgtKey);
          let tids = [];
          if (tgtRaw) { try { tids = JSON.parse(tgtRaw); } catch(e) {} }
          if (!tids.includes(mctx.player.userId)) { tids.push(mctx.player.userId); ext.storageSet(tgtKey, JSON.stringify(tids)); }
          output += `@${targetName} 请做出反应：\n.e 闪避 / .e d  /  .e 反击 / .e c`;
        }
      } else {
        output = `${pn} 使用基础近战攻击【${best.name}=${best.value}】\n（请 @目标 指定攻击对象）`;
      }
    } else {
      // Spell execution: load spells and find spell N
      const spells = getCachedSpells(ctx, gid);
      const spell = spells.find(s => s.index === skillNum);
      if (!spell) {
        seal.replyToSender(ctx, msg, `未找到技能${skillNum}！请先使用 .st 录入法术数据。`);
        return seal.ext.newCmdExecuteResult(true);
      }
      // Check spell timing
      const timing = spell['时机'] || 2;
      isPassive = hasTiming(timing, 1);            // 1 = 被动
      const isMain = hasTiming(timing, 2);       // 2 = 主动作
      const isExtra = hasTiming(timing, 3);      // 3 = 附加动作
      const isReaction = hasTiming(timing, 4);   // 4 = 反应
      if (!isMain && !isPassive) {
        // Only allow 主动作 and 被动 skills in .sN
        seal.replyToSender(ctx, msg, `【${spell.name}】不能在主动作阶段使用（时机: ${TIMING_NAMES[timing]||timing}）`);
        return seal.ext.newCmdExecuteResult(true);
      }
      output = executeSpell(ctx, mctx, spell, gid, isPassive ? 'passive' : 'main');
    }

    // Decrement action (passives don't consume actions)
    if (skillNum === 0 || !isPassive) {
      myActions.主动 -= 1;
    } else {
      output += '\n（被动法术，不消耗主动作）';
    }
    setActions(gid, actions);

    // Advance turn if out of main actions (skip for passives)
    if (!isPassive && myActions.主动 <= 0) {
      state.activeIndex = (state.activeIndex + 1) % initList.length;
      if (state.activeIndex === 0) {
        state.round++;
        // Refresh actions for new round
        const acts = getActions(gid);
        for (const k in acts) { acts[k] = { 附加: 3, 主动: 2 }; }
        setActions(gid, acts);
        output += `\n=== 第 ${state.round} 回合 ===\n`;

        // --- Tick down combat effects (回合后结算) ---
        const effKey = `combat_effects_${gid}`;
        const effRaw = ext.storageGet(effKey);
        if (effRaw) {
          try {
            let effects = JSON.parse(effRaw);
            let expiredMsgs = [];
            effects = effects.filter(eff => {
              // --- Craft countdown (制造剩余回合) ---
              if (eff.type === 'create' && eff.craftRoundsRemaining !== undefined && eff.craftRoundsRemaining > 0) {
                eff.craftRoundsRemaining -= 1;
                if (eff.craftRoundsRemaining <= 0) {
                  expiredMsgs.push(`制造完成：【${eff.template || eff.spellName}】已就绪！可使用 .g ${eff.template || eff.craftId} @目标 转移。`);
                }
              }
              // --- Trigger countdown (引发延迟) ---
              if (eff.type === 'trigger') {
                eff.remainingRounds = (eff.remainingRounds || 0) - 1;
                if (eff.remainingRounds <= 0) {
                  if (eff.persistent) {
                    // End the persistent source spell's effects (形态转换)
                    effects = effects.filter(e => !(e.spellIndex === eff.sourceSpellIndex && e !== eff));
                    expiredMsgs.push(`【${eff.sourceSpellName}】法术形态转换中...`);
                  }
                  expiredMsgs.push(`引发：自动释放技能${eff.triggerSpellIndex}！`);
                  return false;  // remove trigger effect
                }
                return true;  // still counting down
              }
              // --- DOT damage application (before countdown) ---
              if (eff.type === 'dot' && eff.dotDice && eff.targetUserId) {
                const dotDmg = rollDice({}, eff.dotDice);  // ctx-less roll
                if (dotDmg > 0) {
                  const dotResult = absorbDamageWithShield(gid, eff.targetUserId, dotDmg);
                  const actualDotDmg = dotResult.remainingDamage;
                  const hpStore3 = getCombatHP(gid);
                  let dotTargetHP = hpStore3[eff.targetUserId];
                  if (dotTargetHP === undefined) dotTargetHP = 10;
                  dotTargetHP = Math.max(0, dotTargetHP - actualDotDmg);
                  hpStore3[eff.targetUserId] = dotTargetHP;
                  setCombatHP(gid, hpStore3);
                  if (dotResult.absorbedByShield > 0) expiredMsgs.push(...dotResult.shieldMsgs);
                  expiredMsgs.push(`持续伤害【${eff.spellName || ''}】→ ${eff.dotDice}=${dotDmg}，造成 ${actualDotDmg} 点伤害（目标HP: ${dotTargetHP}）`);
                  if (dotTargetHP <= 0) {
                    expiredMsgs.push(`目标因持续伤害死亡，退出战斗！`);
                    // Remove from initiative
                    const dotInit = getInitiative(gid);
                    setInitiative(gid, dotInit.filter(e => e.userId !== eff.targetUserId));
                  }
                }
              }

              // --- Zone effect processing ---
              if (eff.type === 'zone') {
                const mapRaw5 = ext.storageGet(`combat_map_${gid}`);
                if (mapRaw5 && eff.radius > 0) {
                  try {
                    const md5 = JSON.parse(mapRaw5);
                    // Update center if zone follows caster
                    if (eff.centerFollows) {
                      for (const [c, occ] of Object.entries(md5.occupants || {})) {
                        if (occ === eff.sourceUserId) { eff.center = c; break; }
                      }
                    }
                    const cp = parseCoord(eff.center);
                    if (cp && md5.occupants) {
                      const filter = eff.filter || 35;
                      const hasObj1 = hasObject(String(filter), '1');  // self
                      const hasObj2 = hasObject(String(filter), '2');  // ally
                      const hasObj3 = hasObject(String(filter), '3');  // ally group
                      const hasObj4 = hasObject(String(filter), '4');  // enemy
                      const hasObj5 = hasObject(String(filter), '5');  // enemy group
                      const sourceTeam = getCharFaction(gid, eff.sourceUserId) || 'Y';
                      for (const [coord, occId] of Object.entries(md5.occupants)) {
                        const tp = parseCoord(coord);
                        if (!tp) continue;
                        const dist = Math.max(Math.abs(tp.col - cp.col), Math.abs(tp.row - cp.row));  // Chebyshev
                        if (dist > eff.radius) continue;
                        // Check faction filter
                        const occTeam = getCharFaction(gid, occId);
                        const isAlly = (occTeam === sourceTeam);
                        const isSelf = (occId === eff.sourceUserId);
                        if (isSelf && !hasObj1) continue;
                        if (isAlly && !isSelf && !hasObj2 && !hasObj3) continue;
                        if (!isAlly && !hasObj4 && !hasObj5) continue;

                        // Apply zone effects to this occupant
                        if (eff.tickDmg) {
                          const zDmg = rollDice({}, eff.tickDmg);
                          if (zDmg > 0) {
                            const zResult = absorbDamageWithShield(gid, occId, zDmg);
                            const zActual = zResult.remainingDamage;
                            const hpSZ = getCombatHP(gid);
                            let zHP = hpSZ[occId] || 10;
                            zHP = Math.max(0, zHP - zActual);
                            hpSZ[occId] = zHP;
                            setCombatHP(gid, hpSZ);
                            expiredMsgs.push(`领域【${eff.spellName || ''}】→ ${coord} 受到 ${zActual} 点伤害`);
                          }
                        }
                        if (eff.tickHealHp && isAlly) {
                          const zHeal = rollDice({}, eff.tickHealHp);
                          if (zHeal > 0) {
                            const hpSZ = getCombatHP(gid);
                            let zHP = hpSZ[occId] || 10;
                            zHP = Math.min(zHP + zHeal, (eff.maxHp || zHP + zHeal));
                            hpSZ[occId] = zHP;
                            setCombatHP(gid, hpSZ);
                            expiredMsgs.push(`领域【${eff.spellName || ''}】→ ${coord} 回复 ${zHeal} HP`);
                          }
                        }
                        if (eff.tickHealMp && isAlly) {
                          const zMp = rollDice({}, eff.tickHealMp);
                          // MP is tracked indirectly; store as note
                          expiredMsgs.push(`领域【${eff.spellName || ''}】→ ${coord} 回复 ${zMp} MP`);
                        }
                      }
                    }
                  } catch(e) {}
                }
              }

              // --- Regular duration tick-down ---
              eff.remainingRounds = (eff.remainingRounds || 0) - 1;
              if (eff.remainingRounds <= 0) {
                // Persistent effects: don't expire, enter sustained state
                if (eff.persistent) {
                  eff.remainingRounds = -1;  // sentinel for "persistent, no countdown"
                  eff.persistentActive = true;
                  expiredMsgs.push(`持续效果【${eff.spellName || ''}】进入延续状态（使用 .s${eff.spellIndex || '?'} end 手动结束）`);
                  return true;  // keep
                }
                expiredMsgs.push(`${eff.type === 'shield' ? '护盾' : eff.type === 'buff' ? '辅助效果' : eff.type === 'summon' ? '召唤物' : eff.type === 'create' ? '制造物' : '效果'}【${eff.spellName || ''}】已过期`);
                // Remove summon from map
                if (eff.type === 'summon' && eff.summonId) {
                  const mapRaw3 = ext.storageGet(`combat_map_${gid}`);
                  if (mapRaw3) {
                    try {
                      const md3 = JSON.parse(mapRaw3);
                      if (md3.occupants) {
                        for (const [coord, occ] of Object.entries(md3.occupants)) {
                          if (occ === eff.summonId) { delete md3.occupants[coord]; break; }
                        }
                        ext.storageSet(`combat_map_${gid}`, JSON.stringify(md3));
                      }
                    } catch(e) {}
                  }
                }
                // Remove craft from map
                if (eff.type === 'create' && eff.craftId) {
                  const mapRaw4 = ext.storageGet(`combat_map_${gid}`);
                  if (mapRaw4) {
                    try {
                      const md4 = JSON.parse(mapRaw4);
                      if (md4.occupants) {
                        for (const [coord, occ] of Object.entries(md4.occupants)) {
                          if (occ === eff.craftId) { delete md4.occupants[coord]; break; }
                        }
                        ext.storageSet(`combat_map_${gid}`, JSON.stringify(md4));
                      }
                    } catch(e) {}
                  }
                }
                return false;  // remove
              }
              return true;  // keep
            });
            ext.storageSet(effKey, effects.length > 0 ? JSON.stringify(effects) : '');
            if (expiredMsgs.length > 0) {
              output += expiredMsgs.join('\n') + '\n';
            }
            if (effects.length > 0) {
              output += `活跃效果: ${effects.map(e => `${e.spellName||'?'}(${e.remainingRounds}回合)`).join(', ')}\n`;
            }
          } catch(e) {}
        }

        // --- Chant countdown (吟唱回合倒数) ---
        // Each round-end: for each active character, decrement spell 吟唱回合.
        // When 吟唱回合 reaches 0, the spell is ready to execute.
        const chantKey = `combat_chant_${gid}`;
        const chantRaw = ext.storageGet(chantKey);
        let chantData = {};
        if (chantRaw) { try { chantData = JSON.parse(chantRaw); } catch(e) {} }
        const currentUserId = initList[state.activeIndex] ? initList[state.activeIndex].userId : null;
        // Only process the current character's chanting spells once per round
        if (currentUserId) {
          const userChants = chantData[currentUserId] || [];
          let chantMsgs = [];
          chantData[currentUserId] = userChants.filter(sc => {
            sc.remainingChant = (sc.remainingChant || 0) - 1;
            if (sc.remainingChant <= 0) {
              chantMsgs.push(`吟唱完成！【${sc.spellName}】准备就绪，可以释放！`);
              return false;  // remove from chant list
            }
            return true;
          });
          if (chantMsgs.length > 0) output += chantMsgs.join('\n') + '\n';
          if (userChants.length > 0) {
            output += `吟唱中: ${userChants.map(sc => `${sc.spellName}(${sc.remainingChant}回合)`).join(', ')}\n`;
          }
          ext.storageSet(chantKey, JSON.stringify(chantData));
        }
      }
      // --- HP threshold triggers ---
      const thresholdMsgs = checkHpThresholds(gid, getInitiative(gid));
      if (thresholdMsgs.length > 0) output += thresholdMsgs.join('\n') + '\n';

      setCombatState(gid, state);
      const next = initList[state.activeIndex];
      output += `\n下一位: ${next ? next.name : '?'}（${state.round ? '第'+state.round+'回合' : ''}）`;
    } else {
      output += `\n剩余主动作: ${myActions.主动}`;
    }

    seal.replyToSender(ctx, msg, output);
    return seal.ext.newCmdExecuteResult(true);
  };
  return cmd;
}

// ============================================================
//  .a  — 附加动作（移动 / 附加技能）
//  语法: .a m A1  (移动)  /  .a s1  (附加动作释放技能1)
// ============================================================
function makeAdditionalCmd() {
  const cmd = seal.ext.newCmdItemInfo();
  cmd.name = 'a';
  cmd.help =
    '.a m <坐标>  // 移动（消耗一次附加动作）\n' +
    '.a s<序号>    // 附加动作释放技能（须为附加动作时机）';
  cmd.solve = (ctx, msg, cmdArgs) => {
    const gid = ctx.group ? ctx.group.groupId : 'private';

    // --- .setab 2 HTTP path: route additional action through Python backend ---
    const _autoMode = getAutoMode(ctx);
    const _battleId = ext.storageGet(`pvp_battle_${gid}`);
    if (_autoMode >= 2 && _battleId) {
      const _args = cmdArgs.cleanArgs.split(/\s+/).filter(a => a.length > 0);
      if (_args.length < 1) {
        seal.replyToSender(ctx, msg, '用法：.a m <坐标>（移动） 或 .a s<序号>（技能） 或 .a eat [目标]');
        return seal.ext.newCmdExecuteResult(true);
      }
      let _action;
      if (_args[0].toLowerCase() === 'm' && _args.length >= 2) {
        _action = `.a m ${_args[1]}`;
      } else if (_args[0].toLowerCase() === 'eat') {
        _action = '.a eat ' + (_args.slice(1).join(' ') || '');
      } else if (_args[0].toLowerCase() === 'give') {
        _action = '.a give ' + (_args.slice(1).join(' ') || '');
      } else if (/^s\d+$/i.test(_args[0])) {
        _action = `.a ${_args[0]}`;
      } else {
        seal.replyToSender(ctx, msg, '用法：.a m <坐标> 或 .a s<序号> 或 .a eat [目标]');
        return seal.ext.newCmdExecuteResult(true);
      }
      pvpFetch(`/api/pvp/${_battleId}/action`, {
        player_id: ctx.player.userId,
        action: _action,
        target: '',
        args: cmdArgs.cleanArgs,
      }).then(_result => {
        if (_result.error) {
          seal.replyToSender(ctx, msg, `[.setab 2] ${_result.message}`);
          return;
        }
        applyServerChanges(gid, _result);
        let _out = _result.output || '';
        if (_result.needs_reaction && _result.pending_attack) {
          ext.storageSet(`pvp_pending_${gid}`, JSON.stringify(_result.pending_attack));
        }
        if (_result.auto_turns && _result.auto_turns.length > 0) {
          const nodes = _result.auto_turns.map((t, i) => {
            const label = (_result.turn_labels && _result.turn_labels[i]) || `行动 ${i+1}`;
            return `【${label}】\n${t}`;
          });
          sendForwardMessage(ctx, msg, nodes, 'AI 自动战斗');
        }
        if (_result.state && _result.state.phase !== 'active') {
          ext.storageSet(`pvp_battle_${gid}`, '');
          _out += '\n\n=== 战斗结束 ===';
        }
        seal.replyToSender(ctx, msg, _out);
      });
      return seal.ext.newCmdExecuteResult(true);
    }

    const state = getCombatState(gid);
    if (!state || state.phase !== 'active') {
      seal.replyToSender(ctx, msg, '当前不在战斗中！请先使用 .bta start 开始战斗。');
      return seal.ext.newCmdExecuteResult(true);
    }
    const initList = getInitiative(gid);
    const actions = getActions(gid);
    const userId = ctx.player.userId;
    const myActions = actions[userId] || { 附加: 3, 主动: 2 };
    if (myActions.附加 <= 0) {
      seal.replyToSender(ctx, msg, '你的附加动作次数已用尽！');
      return seal.ext.newCmdExecuteResult(true);
    }

    const args = cmdArgs.cleanArgs.split(/\s+/).filter(a => a.length > 0);
    if (args.length < 1) {
      seal.replyToSender(ctx, msg, '用法：.a m <坐标>（移动） 或 .a s<序号>（技能） 或 .a eat [目标]');
      return seal.ext.newCmdExecuteResult(true);
    }

    let out = '';
    const pn = seal.format(ctx, '{$t玩家}');

    if (args[0].toLowerCase() === 'm' && args.length >= 2) {
      // Movement
      const targetCoord = args[1].toUpperCase();
      const coord = parseCoord(targetCoord);
      if (!coord) {
        seal.replyToSender(ctx, msg, `无效坐标: ${targetCoord}（格式如 A1, B3）`);
        return seal.ext.newCmdExecuteResult(true);
      }
      const mapRaw = ext.storageGet(`combat_map_${gid}`);
      if (!mapRaw) {
        seal.replyToSender(ctx, msg, '地图未初始化！请先 .bta map');
        return seal.ext.newCmdExecuteResult(true);
      }
      const mapData = JSON.parse(mapRaw);
      if (!mapData.occupants) mapData.occupants = {};
      // Find current position
      let oldCoord = '';
      for (const [c, occ] of Object.entries(mapData.occupants)) {
        if (occ === userId || occ === pn) { oldCoord = c; break; }
      }
      // Remove from old position
      if (oldCoord) delete mapData.occupants[oldCoord];
      // Place at new position (movement range: MOV or default 3)
      const mov = getAttr(ctx, '行动力', 3);
      if (oldCoord) {
        const old = parseCoord(oldCoord);
        const dist = Math.abs(coord.col - old.col) + Math.abs(coord.row - old.row);
        if (dist > mov) {
          seal.replyToSender(ctx, msg, `移动距离 ${dist} 超过行动力 ${mov}！`);
          return seal.ext.newCmdExecuteResult(true);
        }
      }
      // Check obstacles
      if (mapData.obstacles && mapData.obstacles[targetCoord]) {
        seal.replyToSender(ctx, msg, `${targetCoord} 是障碍物，无法移动到该格！`);
        return seal.ext.newCmdExecuteResult(true);
      }
      // Check faction-based obstacle: different factions cannot share cells
      if (!canEnterCell(mapData, gid, targetCoord, userId)) {
        seal.replyToSender(ctx, msg, `${targetCoord} 已被不同阵营的角色占据，无法移动到该格！`);
        return seal.ext.newCmdExecuteResult(true);
      }
      mapData.occupants[targetCoord] = userId;
      ext.storageSet(`combat_map_${gid}`, JSON.stringify(mapData));
      out = `${pn} 移动: ${oldCoord || '?'} → ${targetCoord}（消耗 1 附加动作，剩余 ${myActions.附加 - 1}）\n`;
      out += renderMap(gid);
    } else if (/^s\d+$/i.test(args[0])) {
      // Additional action spell
      const skillNum = parseInt(args[0].slice(1));
      const spells = getCachedSpells(ctx, gid);
      const spell = spells.find(s => s.index === skillNum);
      if (!spell) {
        seal.replyToSender(ctx, msg, `未找到技能${skillNum}！`);
        return seal.ext.newCmdExecuteResult(true);
      }
      const timing = spell['时机'] || 2;
      if (!hasTiming(timing, 3)) {  // 3 = 附加动作
        seal.replyToSender(ctx, msg, `【${spell.name}】不能在附加动作阶段使用`);
        return seal.ext.newCmdExecuteResult(true);
      }
      const mctx = seal.getCtxProxyFirst(ctx, cmdArgs);
      out = executeSpell(ctx, mctx, spell, gid, 'additional');
    } else {
      seal.replyToSender(ctx, msg, '用法：.a m <坐标> 或 .a s<序号>');
      return seal.ext.newCmdExecuteResult(true);
    }

    // Decrement additional action
    myActions.附加 -= 1;
    setActions(gid, actions);

    out += `\n剩余: 主动×${myActions.主动} 附加×${myActions.附加}`;
    seal.replyToSender(ctx, msg, out);
    return seal.ext.newCmdExecuteResult(true);
  };
  return cmd;
}

// ============================================================
//  .stc <属性> <值>  — 多卡模式 st（支持罗马数字前缀）
//  根据当前绑定的魔法少女序号自动添加前缀。
//  如 .as III 后，.stc 力量 50 → .st III_力量 50
// ============================================================
const cmdStc = seal.ext.newCmdItemInfo();
cmdStc.name = 'stc';
cmdStc.help =
  '.stc <属性> <值> // 多卡模式st（自动添加当前魔法少女序号前缀）\n' +
  '需先使用 .as <序号> 绑定卡片。\n' +
  '如 .as III 后 .stc 力量 50 → 实际设置 III_力量=50';
cmdStc.solve = (ctx, msg, cmdArgs) => {
  const uid = ctx.player.userId;
  const bindRaw = ext.storageGet(`bta_bind_${uid}`);
  if (!bindRaw) {
    seal.replyToSender(ctx, msg, '未绑定魔法少女序号！请先使用 .as <序号> 绑定（如 .as II）');
    return seal.ext.newCmdExecuteResult(true);
  }
  let serial;
  try { serial = JSON.parse(bindRaw).serial; } catch(e) {
    seal.replyToSender(ctx, msg, '绑定数据损坏，请重新 .as <序号>');
    return seal.ext.newCmdExecuteResult(true);
  }
  // Convert to Roman numeral if numeric
  let prefix = serial;
  if (/^\d+$/.test(serial)) {
    const romanNums = ['0','I','II','III','IV','V','VI','VII','VIII','IX','X',
      'XI','XII','XIII','XIV','XV'];
    const n = parseInt(serial);
    prefix = romanNums[n] || serial;
  }

  const args = (cmdArgs.cleanArgs || '').trim();
  if (!args) {
    seal.replyToSender(ctx, msg, '用法：.stc <属性> <值>（如 .stc 力量 50）');
    return seal.ext.newCmdExecuteResult(true);
  }

  // Parse st syntax: "attr value" or "attr+value" or "attr-value" or "attr=text"
  let match = args.match(/^(.+?)[\s:：]+(.+)$/);
  if (match) {
    const attrName = `${prefix}_${match[1].trim()}`;
    const val = match[2].trim();
    // If value is numeric, use intSet; otherwise strSet
    if (/^[+-]?\d+$/.test(val)) {
      seal.vars.intSet(ctx, attrName, parseInt(val));
    } else {
      seal.vars.strSet(ctx, attrName, val);
    }
    seal.replyToSender(ctx, msg, `已设置 ${attrName} = ${val}`);
  } else {
    seal.replyToSender(ctx, msg, '格式错误。用法：.stc <属性> <值>（如 .stc 力量 50 或 .stc 伤害值=1d6）');
  }
  return seal.ext.newCmdExecuteResult(true);
};

// ============================================================
//  Register all commands
// ============================================================
ext.cmdMap['setab']     = cmdSetab;
ext.cmdMap['setrestim'] = cmdSetrestim;
ext.cmdMap['bta']       = makeBtaCmd('bta');
// ============================================================
//  Utility: Forward message (合并聊天记录) for AI turn display
//  Adapted from getSong_v2.js sendForwardMessage
// ============================================================
function _getDigits(str) {
  if (!str) return '';
  const m = String(str).match(/\d+/);
  return m ? m[0] : '';
}

function _makeForwardNode(userId, nickname, text) {
  return {
    type: "node",
    data: {
      user_id: userId,
      nickname: nickname,
      content: {
        type: "text",
        data: { text: String(text || "") },
      },
    },
  };
}

function sendForwardMessage(ctx, msg, texts, summaryTitle) {
  const epId = (ctx && ctx.endPoint && ctx.endPoint.userId) || "";
  const userId = _getDigits(epId) || "10000";
  const nickname = seal.formatTmpl(ctx, "核心:骰子名字") || "骰娘";
  const messages = (texts || []).map((t) => _makeForwardNode(userId, nickname, t));

  const title = summaryTitle || "自动战斗回合";
  const data = {
    messages: messages,
    news: [{ text: title }],
    prompt: title,
    summary: `查看${messages.length}条消息`,
    source: `${nickname}聊天记录`,
  };

  if (ctx.isPrivate) {
    const uid = _getDigits(ctx.player && ctx.player.userId);
    if (uid) data.user_id = uid;
  } else {
    const gid = _getDigits(ctx.group && ctx.group.groupId);
    if (gid) data.group_id = gid;
  }

  try {
    if (typeof http !== "undefined" && http && typeof http.getData === "function") {
      http.getData(epId, "send_forward_msg", data);
      return true;
    }
  } catch (e) {
    // fallback
  }
  const fallback = (texts || []).join("\n\n");
  seal.replyToSender(ctx, msg, fallback);
  return false;
}
// ============================================================

ext.cmdMap['btab']      = makeBtaCmd('btab');
ext.cmdMap['btap']      = makeBtaCmd('btap');
ext.cmdMap['btab2']     = makeBtaCmd('btab2');
ext.cmdMap['btab3']     = makeBtaCmd('btab3');
ext.cmdMap['btap2']     = makeBtaCmd('btap2');
ext.cmdMap['btap3']     = makeBtaCmd('btap3');
ext.cmdMap['e']         = makeECmd('e');
ext.cmdMap['eb']        = makeECmd('eb');
ext.cmdMap['ep']        = makeECmd('ep');
ext.cmdMap['eb2']       = makeECmd('eb2');
ext.cmdMap['eb3']       = makeECmd('eb3');
ext.cmdMap['ep2']       = makeECmd('ep2');
ext.cmdMap['ep3']       = makeECmd('ep3');
ext.cmdMap['hs']        = cmdHs;
ext.cmdMap['unh']       = cmdUnh;
ext.cmdMap['btastart'] = cmdBtaStart;
ext.cmdMap['btaend']   = cmdBtaEnd;
ext.cmdMap['stb']      = cmdStb;
ext.cmdMap['stsave']   = cmdStsave;
ext.cmdMap['as']       = cmdAs;
ext.cmdMap['cm']       = cmdCm;
ext.cmdMap['stc']      = cmdStc;
ext.cmdMap['btaint']   = cmdBtaInt;
ext.cmdMap['a']        = makeAdditionalCmd();
ext.cmdMap['btastartfull'] = cmdBtaStartFull;
ext.cmdMap['btastartfull2'] = cmdBtaStartFull2;
ext.cmdMap['btastt2'] = cmdBtaStartFull2;
ext.cmdMap['btastartfullai'] = cmdBtaStartFullAI;
ext.cmdMap['btastai'] = cmdBtaStartFullAI;
ext.cmdMap['btast'] = cmdBtaStartFull2;  // .btast alias for .btastartfull2


// ============================================================
//  .i end  — 手动结束当前回合 (.setab 1 & .setab 2)
// ============================================================
const cmdI = seal.ext.newCmdItemInfo();
cmdI.name = 'i';
cmdI.help = '.i end // 手动结束当前回合，剩余主动作和附加动作清零，推进至下一角色';
cmdI.solve = (ctx, msg, cmdArgs) => {
  const args = cmdArgs.cleanArgs.split(/\s+/).filter(a => a.length > 0);
  if (args.length < 1 || args[0].toLowerCase() !== 'end') {
    seal.replyToSender(ctx, msg, '用法：.i end // 结束当前回合');
    return seal.ext.newCmdExecuteResult(true);
  }

  const gid = ctx.group ? ctx.group.groupId : 'private';

  // ── .setab 2 HTTP path ──
  const autoMode = getAutoMode(ctx);
  const battleId = ext.storageGet(`pvp_battle_${gid}`);
  if (autoMode >= 2 && battleId) {
    pvpFetch(`/api/pvp/${battleId}/action`, {
      player_id: ctx.player.userId,
      action: '.i end',
      target: '',
      args: '',
    }).then(result => {
      if (result.error) {
        seal.replyToSender(ctx, msg, `[.setab 2] ${result.message}`);
        return;
      }
      applyServerChanges(gid, result);
      const out = result.output || '回合已结束。';
      // Check if battle ended
      if (result.state && result.state.phase !== 'active') {
        ext.storageSet(`pvp_battle_${gid}`, '');
        seal.replyToSender(ctx, msg, out + `\n\n=== 战斗结束 ===\n胜者: ${result.state.winner || '?'}`);
      } else {
        seal.replyToSender(ctx, msg, out);
      }
    });
    return seal.ext.newCmdExecuteResult(true);
  }

  const state = getCombatState(gid);
  if (!state || state.phase !== 'active') {
    seal.replyToSender(ctx, msg, '当前不在战斗中！请先使用 .bta start 开始战斗。');
    return seal.ext.newCmdExecuteResult(true);
  }

  // Verify it's this player's turn
  const initList = getInitiative(gid);
  const actions = getActions(gid);
  const userId = ctx.player.userId;
  const activeEntry = initList[state.activeIndex];
  if (!activeEntry || activeEntry.userId !== userId) {
    seal.replyToSender(ctx, msg, `现在不是你的回合！当前行动: ${activeEntry ? activeEntry.name : '?'}`);
    return seal.ext.newCmdExecuteResult(true);
  }

  const pn = seal.format(ctx, '{$t玩家}');
  let output = `${pn} 主动结束回合。\n`;

  // Clear remaining actions
  const myActions = actions[userId] || { 附加: 0, 主动: 0 };
  const remainingMain = myActions.主动;
  const remainingExtra = myActions.附加;
  myActions.主动 = 0;
  myActions.附加 = 0;
  setActions(gid, actions);

  output += `（剩余主动作×${remainingMain}、附加动作×${remainingExtra} 已清零）\n`;

  // Advance turn
  state.activeIndex = (state.activeIndex + 1) % initList.length;
  if (state.activeIndex === 0) {
    state.round++;
    // Refresh actions for new round
    const acts = getActions(gid);
    for (const k in acts) { acts[k] = { 附加: 3, 主动: 2 }; }
    setActions(gid, acts);
    output += `\n=== 第 ${state.round} 回合 ===\n`;

    // --- Tick down combat effects (回合后结算) ---
    const effKey = `combat_effects_${gid}`;
    const effRaw = ext.storageGet(effKey);
    if (effRaw) {
      try {
        let effects = JSON.parse(effRaw);
        let expiredMsgs = [];
        effects = effects.filter(eff => {
          if (eff.type === 'create' && eff.craftRoundsRemaining !== undefined && eff.craftRoundsRemaining > 0) {
            eff.craftRoundsRemaining -= 1;
            if (eff.craftRoundsRemaining <= 0) {
              expiredMsgs.push(`制造完成：【${eff.template || eff.spellName}】已就绪！可使用 .g ${eff.template || eff.craftId} @目标 转移。`);
            }
          }
          if (eff.type === 'trigger') {
            eff.remainingRounds = (eff.remainingRounds || 0) - 1;
            if (eff.remainingRounds <= 0) {
              if (eff.persistent) {
                effects = effects.filter(e => !(e.spellIndex === eff.sourceSpellIndex && e !== eff));
                expiredMsgs.push(`【${eff.sourceSpellName}】法术形态转换中...`);
              }
              expiredMsgs.push(`引发：自动释放技能${eff.triggerSpellIndex}！`);
              return false;
            }
            return true;
          }
          // --- DOT damage application (before countdown) ---
          if (eff.type === 'dot' && eff.dotDice && eff.targetUserId) {
            const dotDmg = rollDice({}, eff.dotDice);
            if (dotDmg > 0) {
              const dotResult = absorbDamageWithShield(gid, eff.targetUserId, dotDmg);
              const actualDotDmg = dotResult.remainingDamage;
              const hpStore3 = getCombatHP(gid);
              let dotTargetHP = hpStore3[eff.targetUserId];
              if (dotTargetHP === undefined) dotTargetHP = 10;
              dotTargetHP = Math.max(0, dotTargetHP - actualDotDmg);
              hpStore3[eff.targetUserId] = dotTargetHP;
              setCombatHP(gid, hpStore3);
              if (dotResult.absorbedByShield > 0) expiredMsgs.push(...dotResult.shieldMsgs);
              expiredMsgs.push(`持续伤害【${eff.spellName || ''}】→ ${eff.dotDice}=${dotDmg}，造成 ${actualDotDmg} 点伤害（目标HP: ${dotTargetHP}）`);
            }
          }
          // --- Zone effect processing ---
          if (eff.type === 'zone') {
            const mapRaw6 = ext.storageGet(`combat_map_${gid}`);
            if (mapRaw6 && eff.radius > 0) {
              try {
                const md6 = JSON.parse(mapRaw6);
                if (eff.centerFollows) {
                  for (const [c, occ] of Object.entries(md6.occupants || {})) {
                    if (occ === eff.sourceUserId) { eff.center = c; break; }
                  }
                }
                const cp2 = parseCoord(eff.center);
                if (cp2 && md6.occupants) {
                  const filter = eff.filter || 35;
                  const hasObj1 = hasObject(String(filter), '1');
                  const hasObj2 = hasObject(String(filter), '2');
                  const hasObj3 = hasObject(String(filter), '3');
                  const hasObj4 = hasObject(String(filter), '4');
                  const hasObj5 = hasObject(String(filter), '5');
                  const sourceTeam = getCharFaction(gid, eff.sourceUserId) || 'Y';
                  for (const [coord, occId] of Object.entries(md6.occupants)) {
                    const tp = parseCoord(coord);
                    if (!tp) continue;
                    const dist = Math.max(Math.abs(tp.col - cp2.col), Math.abs(tp.row - cp2.row));
                    if (dist > eff.radius) continue;
                    const occTeam = getCharFaction(gid, occId);
                    const isAlly = (occTeam === sourceTeam);
                    const isSelf = (occId === eff.sourceUserId);
                    if (isSelf && !hasObj1) continue;
                    if (isAlly && !isSelf && !hasObj2 && !hasObj3) continue;
                    if (!isAlly && !hasObj4 && !hasObj5) continue;
                    if (eff.tickDmg) {
                      const zDmg = rollDice({}, eff.tickDmg);
                      if (zDmg > 0) {
                        const zResult = absorbDamageWithShield(gid, occId, zDmg);
                        const zActual = zResult.remainingDamage;
                        const hpSZ = getCombatHP(gid);
                        let zHP = hpSZ[occId] || 10;
                        zHP = Math.max(0, zHP - zActual);
                        hpSZ[occId] = zHP;
                        setCombatHP(gid, hpSZ);
                        expiredMsgs.push(`领域【${eff.spellName || ''}】→ ${coord} 受到 ${zActual} 点伤害`);
                      }
                    }
                    if (eff.tickHealHp && isAlly) {
                      const zHeal = rollDice({}, eff.tickHealHp);
                      if (zHeal > 0) {
                        const hpSZ = getCombatHP(gid);
                        let zHP = hpSZ[occId] || 10;
                        zHP = Math.min(zHP + zHeal, zHP + zHeal);
                        hpSZ[occId] = zHP;
                        setCombatHP(gid, hpSZ);
                        expiredMsgs.push(`领域【${eff.spellName || ''}】→ ${coord} 回复 ${zHeal} HP`);
                      }
                    }
                  }
                }
              } catch(e) {}
            }
          }
          eff.remainingRounds = (eff.remainingRounds || 0) - 1;
          if (eff.remainingRounds <= 0) {
            if (eff.persistent) {
              eff.remainingRounds = -1;
              eff.persistentActive = true;
              expiredMsgs.push(`持续效果【${eff.spellName || ''}】进入延续状态（使用 .s${eff.spellIndex || '?'} end 手动结束）`);
              return true;
            }
            expiredMsgs.push(`${eff.type === 'shield' ? '护盾' : eff.type === 'buff' ? '辅助效果' : eff.type === 'summon' ? '召唤物' : eff.type === 'create' ? '制造物' : '效果'}【${eff.spellName || ''}】已过期`);
            if (eff.type === 'summon' && eff.summonId) {
              const mapRaw3 = ext.storageGet(`combat_map_${gid}`);
              if (mapRaw3) {
                try {
                  const md3 = JSON.parse(mapRaw3);
                  if (md3.occupants) {
                    for (const [coord, occ] of Object.entries(md3.occupants)) {
                      if (occ === eff.summonId) { delete md3.occupants[coord]; break; }
                    }
                    ext.storageSet(`combat_map_${gid}`, JSON.stringify(md3));
                  }
                } catch(e) {}
              }
            }
            if (eff.type === 'create' && eff.craftId) {
              const mapRaw4 = ext.storageGet(`combat_map_${gid}`);
              if (mapRaw4) {
                try {
                  const md4 = JSON.parse(mapRaw4);
                  if (md4.occupants) {
                    for (const [coord, occ] of Object.entries(md4.occupants)) {
                      if (occ === eff.craftId) { delete md4.occupants[coord]; break; }
                    }
                    ext.storageSet(`combat_map_${gid}`, JSON.stringify(md4));
                  }
                } catch(e) {}
              }
            }
            return false;
          }
          return true;
        });
        ext.storageSet(effKey, effects.length > 0 ? JSON.stringify(effects) : '');
        if (expiredMsgs.length > 0) {
          output += expiredMsgs.join('\n') + '\n';
        }
        if (effects.length > 0) {
          output += `活跃效果: ${effects.map(e => `${e.spellName||'?'}(${e.remainingRounds}回合)`).join(', ')}\n`;
        }
      } catch(e) {}
    }

    // --- Chant countdown (吟唱回合倒数) ---
    const chantKey = `combat_chant_${gid}`;
    const chantRaw = ext.storageGet(chantKey);
    if (chantRaw) {
      try {
        let chantData = JSON.parse(chantRaw);
        const currentUserId = initList[state.activeIndex] ? initList[state.activeIndex].userId : null;
        if (currentUserId) {
          const userChants = chantData[currentUserId] || [];
          let chantMsgs = [];
          chantData[currentUserId] = userChants.filter(sc => {
            sc.remainingChant = (sc.remainingChant || 0) - 1;
            if (sc.remainingChant <= 0) {
              chantMsgs.push(`吟唱完成！【${sc.spellName}】准备就绪，可以释放！`);
              return false;
            }
            return true;
          });
          if (chantMsgs.length > 0) output += chantMsgs.join('\n') + '\n';
          if (userChants.length > 0) {
            output += `吟唱中: ${userChants.map(sc => `${sc.spellName}(${sc.remainingChant}回合)`).join(', ')}\n`;
          }
          ext.storageSet(chantKey, JSON.stringify(chantData));
        }
      } catch(e) {}
    }
  }
  // --- HP threshold triggers ---
  const thresholdMsgs2 = checkHpThresholds(gid, getInitiative(gid));
  if (thresholdMsgs2.length > 0) output += thresholdMsgs2.join('\n') + '\n';

  setCombatState(gid, state);
  const next = initList[state.activeIndex];
  output += `\n下一位: ${next ? next.name : '?'}（${state.round ? '第'+state.round+'回合' : ''}）`;

  seal.replyToSender(ctx, msg, output);
  return seal.ext.newCmdExecuteResult(true);
};
ext.cmdMap['i'] = cmdI;
// Register .s0 through .s9 skill commands
for (let si = 0; si <= 9; si++) {
  ext.cmdMap[`s${si}`] = makeSkillCmd(si);
}

// .c — 主动作前缀命令（.c sN 委托到 .sN）
const cmdC = seal.ext.newCmdItemInfo();
cmdC.name = 'c';
cmdC.help = '.c s<序号> // 主动作：使用技能（等同于直接 .s<序号>）';
cmdC.allowDelegate = true;
cmdC.solve = (ctx, msg, cmdArgs) => {
  const args = cmdArgs.cleanArgs.split(/\s+/).filter(a => a.length > 0);
  if (args.length < 1 || !/^s\d+$/i.test(args[0])) {
    seal.replyToSender(ctx, msg, '用法：.c s<序号>（如 .c s2, .c s0）');
    return seal.ext.newCmdExecuteResult(true);
  }
  // Delegate to the corresponding .sN command
  const skillCmd = ext.cmdMap[args[0].toLowerCase()];
  if (skillCmd && skillCmd.solve) {
    // Rewrite cmdArgs to strip the .c prefix
    const newArgs = cmdArgs.cleanArgs.replace(/^s\d+\s*/, '');
    cmdArgs.cleanArgs = args[0];
    return skillCmd.solve(ctx, msg, cmdArgs);
  }
  seal.replyToSender(ctx, msg, `未找到命令 ${args[0]}`);
  return seal.ext.newCmdExecuteResult(true);
};
ext.cmdMap['c'] = cmdC;

// ============================================================
//  .g <物品名> @目标  — 制造物转移
//  将已完成的制造物转移给目标角色，写入HP/MP/SAN/技能属性。
// ============================================================
const cmdG = seal.ext.newCmdItemInfo();
cmdG.name = 'g';
cmdG.help =
  '.g <物品名> @目标 // 将制造物转移给目标角色\n' +
  '.g 魔法药剂 @张三  // 将【魔法药剂】转移给张三\n' +
  '转移后，物品属性（HP/MP/SAN/技能）写入目标角色卡。\n' +
  '需在战斗中且制造物已就绪（制造回合数归零后）使用。';
cmdG.allowDelegate = true;
cmdG.solve = (ctx, msg, cmdArgs) => {
  const gid = ctx.group ? ctx.group.groupId : 'private';
  const state = getCombatState(gid);
  if (!state || state.phase !== 'active') {
    seal.replyToSender(ctx, msg, '当前不在战斗中！请先使用 .bta start 开始战斗。');
    return seal.ext.newCmdExecuteResult(true);
  }

  const mctx = seal.getCtxProxyFirst(ctx, cmdArgs);
  if (!mctx || !mctx.player) {
    seal.replyToSender(ctx, msg, '请 @目标 指定转移对象！格式：.g <物品名> @目标');
    return seal.ext.newCmdExecuteResult(true);
  }

  const args = cmdArgs.cleanArgs.split(/\s+/).filter(a => a.length > 0);
  if (args.length < 1) {
    seal.replyToSender(ctx, msg, '用法：.g <物品名> @目标');
    return seal.ext.newCmdExecuteResult(true);
  }
  const itemName = args[0];

  // Find craft effect owned by ctx.player
  const effKey = `combat_effects_${gid}`;
  const raw = ext.storageGet(effKey);
  if (!raw) {
    seal.replyToSender(ctx, msg, '没有活跃的制造物！');
    return seal.ext.newCmdExecuteResult(true);
  }
  let effects;
  try { effects = JSON.parse(raw); } catch(e) {
    seal.replyToSender(ctx, msg, '效果数据损坏。');
    return seal.ext.newCmdExecuteResult(true);
  }

  // Find matching craft item
  const craftIdx = effects.findIndex(eff =>
    eff.type === 'create' &&
    eff.sourceUserId === ctx.player.userId &&
    (eff.template === itemName || eff.craftId === itemName) &&
    (eff.craftRoundsRemaining === undefined || eff.craftRoundsRemaining <= 0)
  );
  if (craftIdx === -1) {
    seal.replyToSender(ctx, msg, `未找到你的制造物【${itemName}】，或该物品尚未完成制造。`);
    return seal.ext.newCmdExecuteResult(true);
  }

  const craft = effects[craftIdx];

  // Transfer: write craft attributes to target character card
  const templates = getCachedCraftTemplates(ctx, gid);
  const tmpl = templates.find(t => t.name === craft.template);
  let out = `${seal.format(ctx, '{$t玩家}')} 将【${craft.template || itemName}】转移给 ${seal.format(mctx, '{$t玩家}')}\n`;
  if (tmpl) {
    // Write base attributes
    if (tmpl.HP !== undefined) seal.vars.intSet(mctx, '体力', tmpl.HP);
    if (tmpl.MP !== undefined) seal.vars.intSet(mctx, '魔力', tmpl.MP);
    if (tmpl.SAN !== undefined) seal.vars.intSet(mctx, '理智', tmpl.SAN);
    if (tmpl.STR !== undefined) seal.vars.intSet(mctx, '力量', tmpl.STR);
    if (tmpl.CON !== undefined) seal.vars.intSet(mctx, '体质', tmpl.CON);
    if (tmpl.SIZ !== undefined) seal.vars.intSet(mctx, '体型', tmpl.SIZ);
    if (tmpl.DEX !== undefined) seal.vars.intSet(mctx, '敏捷', tmpl.DEX);
    if (tmpl.APP !== undefined) seal.vars.intSet(mctx, '外貌', tmpl.APP);
    if (tmpl.INT !== undefined) seal.vars.intSet(mctx, '智力', tmpl.INT);
    if (tmpl.POW !== undefined) seal.vars.intSet(mctx, '意志', tmpl.POW);
    if (tmpl.EDU !== undefined) seal.vars.intSet(mctx, '教育', tmpl.EDU);
    // Write skills as item-specific attributes
    for (let si = 0; si < tmpl.skills.length; si++) {
      const skStr = tmpl.skills[si];
      seal.vars.strSet(mctx, `物品技能_${itemName}_${si+1}`, skStr);
    }
    out += `已写入属性: HP${tmpl.HP||'?'} MP${tmpl.MP||'?'} SAN${tmpl.SAN||'?'}`;
    if (tmpl.skills.length > 0) out += ` 技能: ${tmpl.skills.join(', ')}`;
    out += '\n';
  }

  // Remove craft from effects
  effects.splice(craftIdx, 1);
  ext.storageSet(effKey, effects.length > 0 ? JSON.stringify(effects) : '');

  seal.replyToSender(ctx, msg, out);
  return seal.ext.newCmdExecuteResult(true);
};

// .u — 物品使用命令（框架）
const cmdU = seal.ext.newCmdItemInfo();
cmdU.name = 'u';
cmdU.help = '.u <物品名> [能力序号] // 使用物品的能力（如 .u 治疗药水）';
cmdU.solve = (ctx, msg, cmdArgs) => {
  const args = cmdArgs.cleanArgs.split(/\s+/).filter(a => a.length > 0);
  if (args.length < 1) {
    seal.replyToSender(ctx, msg, '用法：.u <物品名> [能力序号]');
    return seal.ext.newCmdExecuteResult(true);
  }
  const itemName = args[0];
  // Look up item from character card
  const itemVal = getStrAttr(ctx, `物品_${itemName}`) || getStrAttr(ctx, itemName);
  if (!itemVal) {
    seal.replyToSender(ctx, msg, `未找到物品【${itemName}】。请先使用 .st 录入物品属性。`);
    return seal.ext.newCmdExecuteResult(true);
  }
  seal.replyToSender(ctx, msg, `${seal.format(ctx, '{$t玩家}')} 使用物品【${itemName}】：${itemVal}`);
  return seal.ext.newCmdExecuteResult(true);
};
ext.cmdMap['g'] = cmdG;
ext.cmdMap['u'] = cmdU;

// Hook: track spell count / combat flag during transformation,
//       and real-time .st attribute sync during combat
ext.onCommandReceived = (ctx, msg, cmdArgs) => {
  // Use mctx (the @mentioned target) to read transformed/combat state,
  // because .hs / .bta start set flags on mctx, not ctx
  const mctx = seal.getCtxProxyFirst(ctx, cmdArgs);
  const cmd = cmdArgs.command.toLowerCase();

  // --- Transformed state tracking ---
  const transformedV = seal.vars.intGet(mctx, '$gHsTransformed');
  if (transformedV[1] && transformedV[0]) {
    if (cmd === 'bta' || cmd === 'btab' || cmd === 'btap' ||
        cmd === 'btab2' || cmd === 'btab3' || cmd === 'btap2' || cmd === 'btap3' ||
        cmd === 'e' || cmd === 'eb' || cmd === 'ep' ||
        cmd === 'eb2' || cmd === 'eb3' || cmd === 'ep2' || cmd === 'ep3' ||
        /^s[0-9]/i.test(cmd) || cmd === 'a' || cmd === 'g' || cmd === 'i') {
      const flagV = seal.vars.intGet(mctx, '$gHsCombatFlag');
      seal.vars.intSet(mctx, '$gHsCombatFlag', (flagV[1] ? flagV[0] : 0) + 1);
    }
    if (/^s[0-9]/i.test(cmd) || cmd === 'cast' || cmd === 'spell') {
      const cntV = seal.vars.intGet(mctx, '$gHsSpellCount');
      seal.vars.intSet(mctx, '$gHsSpellCount', (cntV[1] ? cntV[0] : 0) + 1);
      // Track per-spell usage for proficiency
      if (/^s(\d+)$/i.test(cmd)) {
        const spellIdx = parseInt(cmd.match(/^s(\d+)$/i)[1]);
        const trackKey = `$gHsSpellUsed_${spellIdx}`;
        const usedV = seal.vars.intGet(mctx, trackKey);
        seal.vars.intSet(mctx, trackKey, (usedV[1] ? usedV[0] : 0) + 1);
      }
    }
  }

  // --- Real-time .st attribute sync during active combat ---
  const combatV = seal.vars.intGet(mctx, '$gCombatActive');
  if (combatV[1] && combatV[0] && mctx && mctx.player) {
    if (cmd === 'st') {
      const args = (cmdArgs.cleanArgs || '').trim();
      if (args) {
        const groupId = mctx.group ? mctx.group.groupId : 'private';
        // COC7 .st format: concatenated key-value pairs without separators.
        // e.g. "体力13魔力11理智65" or "体力+5" or "体力-3"
        // Scan for known attribute names and extract the following signed number.
        const hpPatterns = ['体力','hp','HP'];
        const mpPatterns = ['魔力','mp','MP'];
        const sanPatterns = ['理智','san','SAN'];
        const allPatterns = [...hpPatterns, ...mpPatterns, ...sanPatterns];
        for (const pat of allPatterns) {
          // Find the attribute name in the string
          const idx = args.indexOf(pat);
          if (idx === -1) continue;
          // Extract the value that follows: optional sign + digits
          const after = args.slice(idx + pat.length);
          const valMatch = after.match(/^([+-]?\d+)/);
          if (!valMatch) continue;
          const valStr = valMatch[1];
          const delta = parseInt(valStr);
          if (isNaN(delta)) continue;
          const isHP = hpPatterns.includes(pat);
          const isMP = mpPatterns.includes(pat);
          const isSAN = sanPatterns.includes(pat);
          const hpStore = getCombatHP(groupId);
          const uid = mctx.player.userId;
          let storeKey, curVal;
          if (isHP) { storeKey = uid; curVal = hpStore[uid]; }
          else if (isMP) { storeKey = 'mp_' + uid; curVal = hpStore[storeKey]; }
          else { storeKey = 'san_' + uid; curVal = hpStore[storeKey]; }
          if (curVal === undefined) {
            const cv = seal.vars.intGet(mctx, pat);
            curVal = cv[1] ? cv[0] : (isSAN ? 50 : 10);
          }
          if (valStr.startsWith('+') || valStr.startsWith('-')) {
            hpStore[storeKey] = Math.max(0, curVal + delta);
          } else {
            hpStore[storeKey] = Math.max(0, delta);
          }
          setCombatHP(groupId, hpStore);
        }
      }
    }
  }
};

console.log('[autocombat] 半自动战斗扩展 v0.7.0 已加载');
console.log('[autocombat] 指令: .setab .setrestim .btastart/.btaend .bta .e .hs .unh .stb .stsave .as .cm .a .s0-.s9 .btaint .btastartfull .btastartfull2 .btastartfullai .g .i end');
console.log('[autocombat] v0.7.0: 召唤物战斗系统、领域效果、DOT持续伤害、Buff/Debuff应用、护盾吸收、吸血、HP阈值触发');
