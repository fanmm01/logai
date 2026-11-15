// Minimal Node regression for autocombat.js dice/attribute/zone helpers.
// Usage: node tests/js_rules_check.js
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'autocombat.js'), 'utf8');

// Extract only the pure helper functions we need (no seal runtime required).
function extract(name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`missing ${name}`);
  let depth = 0, i = src.indexOf('{', start);
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') { depth--; if (depth === 0) return src.slice(start, j + 1); }
  }
  throw new Error(`unterminated ${name}`);
}

const auxBlock = src.match(/const AUX_EFFECT_TYPES = \{[\s\S]*?\};/)[0];
const auxMap = src.match(/const AUX_EFFECT_NAMES_TO_CODE = \{\};[\s\S]*?\}/)[0];
const movGroup = src.match(/const MOV_ATTR_GROUP = \[[^\]]*\];/)[0];
const zoneUnsupported = src.match(/const ZONE_UNSUPPORTED_AUX = \[[\s\S]*?\];/)[0];

// In-memory SealDice storage stub.
const storage = {};
const ext = {
  storageGet: (k) => (k in storage ? storage[k] : null),
  storageSet: (k, v) => { storage[k] = v; },
};

// The zone helpers call getActiveBuffs/applyBuffAttrMod, which reference storage.
const code = [
  auxBlock, auxMap, movGroup, zoneUnsupported,
  extract('parseBpToken'),
  extract('formatBp'),
  extract('calcNetBp'),
  extract('parseAttrMods'),
  extract('attrGroup'),
  extract('parseCoord'),
  extract('parseObjCodes'),
  extract('zoneIncludes'),
  extract('hasPhysicalIsolation'),
  extract('zoneCenter'),
  extract('zoneCoversUid'),
  extract('zoneBuffViews'),
  extract('zoneAttrPenalty'),
  extract('applyBuffAttrMod'),
  extract('applyBuffSkillMod'),
  extract('getBuffSkillMult'),
  'function getActiveBuffs(gid, uid) {',
  '  const effects = JSON.parse(ext.storageGet(`combat_effects_${gid}`) || "[]");',
  '  const physical = effects.filter(e => (e.type === "buff" || e.type === "debuff") &&',
  '    e.targetUserId === uid && (e.remainingRounds || 0) !== 0);',
  '  const views = zoneBuffViews(gid, uid, effects);',
  '  return views.length ? physical.concat(views) : physical;',
  '}',
  'function getInitiative(gid) { return JSON.parse(ext.storageGet(`combat_initiative_${gid}`) || "[]"); }',
  'function getCharFaction(gid, uid) {',
  '  const e = getInitiative(gid).find(x => x.userId === uid);',
  '  return e ? (e.team || null) : null;',
  '}',
  'function hasStatus(gid, uid, name) {',
  '  return getActiveBuffs(gid, uid).some(b => b.auxType === name);',
  '}',
  'module.exports = {parseBpToken, formatBp, calcNetBp, parseAttrMods, attrGroup,',
  '  parseObjCodes, zoneIncludes, zoneCoversUid, zoneBuffViews, getActiveBuffs,',
  '  zoneAttrPenalty, applyBuffAttrMod, applyBuffSkillMod};',
].join('\n');

const m = {exports:{}};
new Function('module', 'exports', 'ext', 'storage', code)(m, m.exports, ext, storage);
const {
  parseBpToken, calcNetBp, parseAttrMods, attrGroup, parseObjCodes, zoneIncludes,
  zoneCoversUid, zoneBuffViews, getActiveBuffs, zoneAttrPenalty, applyBuffSkillMod,
} = m.exports;

let pass = 0, fail = 0;
function eq(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) pass++; else { fail++; console.log(`FAIL ${label}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`); }
}

eq('token b2', parseBpToken('b2'), 2);
eq('token 2p', parseBpToken('2p'), -2);
eq('p+p', calcNetBp([], '', null, ['p','p']), 'p2');
eq('p+b', calcNetBp([], '', null, ['p','b']), '');
eq('b5+p3 clamp', calcNetBp([], '', null, ['b5','p3']), 'b2');
eq('aux16+aux18 generic', calcNetBp([
  {auxCode:16, auxVal:'b'},
  {auxCode:18, auxVal:'p'},
  {auxCode:18, auxVal:'p2'},
], '', '闪避'), 'p2');
eq('aux18 named', calcNetBp([{auxCode:18, auxVal:'斗殴'}], '', '斗殴'), 'p');
eq('aux18 named other', calcNetBp([{auxCode:18, auxVal:'斗殴'}], '', '闪避'), '');
eq('no double count', calcNetBp([{auxCode:18, auxVal:'p', auxType:'技能惩罚骰'}], '', null), 'p');
eq('ignore global', calcNetBp([{auxCode:16, auxVal:'b'}], '', null, ['p'], true), 'p');
eq('attr parse', parseAttrMods('MOV+5;闪避-30'), [{name:'MOV',delta:5},{name:'闪避',delta:-30}]);
eq('mov group', attrGroup('行动力'), 'MOV');
eq('aux33 registered', /33:\s*'反应惩罚骰'/.test(auxBlock), true);

// ---- 客体 token parsing / zone eligibility ----
eq('tokens 135', parseObjCodes('135'), ['135']);
eq('tokens 45', parseObjCodes('45'), ['45']);
eq('tokens 24', parseObjCodes('24'), ['24']);
eq('tokens R', parseObjCodes('R'), ['R']);
eq('default code', parseObjCodes(undefined), ['4']);

const GID = 'testgroup';
function reset() {
  for (const k of Object.keys(storage)) delete storage[k];
  storage[`combat_initiative_${GID}`] = JSON.stringify([
    {userId:'A', team:'Y', coord:'F6'},
    {userId:'B', team:'Y', coord:'G6'},
    {userId:'C', team:'X', coord:'H6'},
    {userId:'D', team:'Y', coord:'N14'},
  ]);
  storage[`combat_map_${GID}`] = JSON.stringify({
    occupants: {F6:'A', G6:'B', H6:'C', N14:'D'} });
}
function setEffects(effects) {
  storage[`combat_effects_${GID}`] = JSON.stringify(effects);
}
function zone(name, filter, extra) {
  return Object.assign({type:'zone', center:'F6', radius:5, remainingRounds:3,
    filter, sourceUserId:'A', spellName:name, spellIndex:90, stackable:0}, extra || {});
}

reset();
setEffects([zone('自身', 1, {auxType:'伤害成功率奖励惩罚', auxVal:'b', auxCode:16, effectKey:'0'})]);
eq('obj1 self', zoneIncludes(1, 'A', 'A', GID), true);
eq('obj1 ally', zoneIncludes(1, 'B', 'A', GID), false);
eq('obj3 excludes self', zoneIncludes(3, 'A', 'A', GID), false);
eq('obj3 ally', zoneIncludes(3, 'B', 'A', GID), true);
eq('obj35 includes self', zoneIncludes(35, 'A', 'A', GID), true);
eq('obj45 enemy', zoneIncludes(45, 'C', 'A', GID), true);
eq('obj45 ally', zoneIncludes(45, 'B', 'A', GID), false);
eq('obj15 no allies', zoneIncludes(15, 'B', 'A', GID), false);
eq('obj15 self', zoneIncludes(15, 'A', 'A', GID), true);
eq('obj24 ally', zoneIncludes(24, 'B', 'A', GID), true);
eq('obj124 ally', zoneIncludes(124, 'B', 'A', GID), true);
eq('objR never covers', zoneIncludes('R', 'C', 'A', GID), false);

eq('covers in radius', zoneCoversUid(GID, zone('z', 1, {}), 'A'), true);
eq('covers out of radius', zoneCoversUid(GID, zone('z', 1, {}), 'D'), false);

// Live membership: zone views follow current coordinates, no tick needed.
eq('view on caster', zoneBuffViews(GID, 'A').length, 1);
eq('view net bp', calcNetBp(getActiveBuffs(GID, 'A'), '', '闪避'), 'b');
storage[`combat_initiative_${GID}`] = JSON.stringify([
  {userId:'A', team:'Y', coord:'L10'},
  {userId:'B', team:'Y', coord:'F6'},
  {userId:'C', team:'X', coord:'H6'},
  {userId:'D', team:'Y', coord:'N14'},
]);
eq('fix-centre no longer covers', zoneBuffViews(GID, 'A').length, 0);
eq('other ally not covered (obj1)', zoneBuffViews(GID, 'B').length, 0);

// Follow-zone reads caster position live.
reset();
setEffects([zone('跟随', 3, {centerFollows:1, auxType:'伤害成功率奖励惩罚',
  auxVal:'b', auxCode:16, effectKey:'0'})]);
eq('follow zone ally', calcNetBp(getActiveBuffs(GID, 'B'), '', '闪避'), 'b');
storage[`combat_initiative_${GID}`] = JSON.stringify([
  {userId:'A', team:'Y', coord:'A1'},
  {userId:'B', team:'Y', coord:'L10'},
  {userId:'C', team:'X', coord:'H6'},
  {userId:'D', team:'Y', coord:'N14'},
]);
eq('follow moves away', calcNetBp(getActiveBuffs(GID, 'B'), '', '闪避'), '');

// Stacking identity: same name/effect dedupes; different names stack;
// stackable instances each contribute.
reset();
setEffects([
  zone('同名', 1, {auxType:'伤害成功率奖励惩罚', auxVal:'b', auxCode:16, effectKey:'0'}),
  zone('同名', 1, {auxType:'伤害成功率奖励惩罚', auxVal:'b', auxCode:16, effectKey:'0'}),
]);
eq('same name dedupe', calcNetBp(getActiveBuffs(GID, 'A'), '', '闪避'), 'b');
setEffects([
  zone('甲', 1, {auxType:'伤害成功率奖励惩罚', auxVal:'b', auxCode:16, effectKey:'a'}),
  zone('乙', 1, {auxType:'伤害成功率奖励惩罚', auxVal:'b', auxCode:16, effectKey:'b'}),
]);
eq('different names stack', calcNetBp(getActiveBuffs(GID, 'A'), '', '闪避'), 'b2');
setEffects([
  zone('甲', 1, {stackable:1, auxType:'伤害成功率奖励惩罚', auxVal:'b', auxCode:16, effectKey:'a'}),
  zone('甲', 1, {stackable:1, auxType:'伤害成功率奖励惩罚', auxVal:'b', auxCode:16, effectKey:'a'}),
]);
eq('stackable instances sum', calcNetBp(getActiveBuffs(GID, 'A'), '', '闪避'), 'b2');

// Unsupported AUX is refused; named skillMod still applies.
reset();
setEffects([zone('免疫', 1, {auxType:'免疫一次伤害', auxVal:'1', auxCode:30,
  effectKey:'0', skillMod:'闪避+10'})]);
eq('unsupported aux ignored', calcNetBp(getActiveBuffs(GID, 'A'), '', '闪避'), '');
eq('skillMod survives', applyBuffSkillMod(GID, 'A', 50, '闪避'), 60);
eq('skillMod other skill', applyBuffSkillMod(GID, 'A', 50, '斗殴'), 50);

// Isolation blocks zone membership without recursion.
reset();
setEffects([
  zone('光环', 1, {auxType:'伤害成功率奖励惩罚', auxVal:'b', auxCode:16, effectKey:'0'}),
  {type:'buff', auxType:'隔离', auxVal:'0', targetUserId:'A', remainingRounds:2},
]);
eq('isolated gets no zone view', calcNetBp(getActiveBuffs(GID, 'A'), '', '闪避'), '');

// Zone attribute penalty uses the same 客体 predicate (35 includes self in JS now).
reset();
setEffects([zone('减闪', 45, {attributeDebuff:'闪避-30', effectKey:'0'})]);
eq('attr penalty enemy', zoneAttrPenalty(GID, 'C', '闪避'), -30);
eq('attr penalty ally', zoneAttrPenalty(GID, 'B', '闪避'), 0);
setEffects([zone('减闪', 35, {attributeDebuff:'闪避-30', effectKey:'0'})]);
eq('attr penalty 35 self', zoneAttrPenalty(GID, 'A', '闪避'), -30);

console.log(`JS helpers: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
