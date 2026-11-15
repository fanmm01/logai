// Match the battle engine's .hs transformation before passive/status bonuses.
const combatSkills = ['格斗', '斗殴', '斧', '链锯', '连枷', '绞索', '矛', '剑', '鞭', '射击', '射击:弓', '射击:手枪', '射击:重武器', '射击:火焰喷射器', '射击:机枪', '射击:步霰', '射击:冲锋枪', '投掷', '爆破', '炮术'];
const dbTable: Record<number, string> = { [-2]: '-2', [-1]: '-1', 0: '0', 1: '1d4', 2: '1d6', 3: '2d6', 4: '3d6', 5: '4d6' };
type Stat = { label: string; before: number | string | undefined; after: number | string | undefined };
export type DamageAttrs = { db?: string; damage_suffix_map?: Record<string, string> | string; [key: string]: unknown };
type AttackSpell = { name?: string; effects?: { 伤害骰?: string }[] };

// Keep substitution consistent with battle_engine._resolve_db, including half db.
function resolveDb(expression: string, db: string): string {
  if (db === '0') {
    return expression.replace(/[+-]?\s*\bdb\b(?:\s*\/\s*\d+)?/gi, '').trim().replace(/^\+\s*/, '') || '0';
  }
  const resolved = expression.replace(/(?<!-)\+?\s*\bdb\b/gi, db.startsWith('-') ? db : `+${db}`)
    .replace(/-\s*\bdb\b/gi, db.startsWith('-') ? `+${Math.abs(Number(db))}` : `-${db}`);
  return resolved.replace(/^\+/, '');
}

function damageExpressions(strAttrs: DamageAttrs, serial: string, spells: AttackSpell[]): [string, string][] {
  let suffixMap: Record<string, string> = { 剑: 'a', 斗殴: 'b' };
  let override = strAttrs.damage_suffix_map;
  if (typeof override === 'string') {
    try { override = JSON.parse(override); } catch { override = undefined; }
  }
  if (override && typeof override === 'object' && !Array.isArray(override) && Object.keys(override).length) suffixMap = override;
  const variants: [string, string][] = Object.entries(strAttrs)
    .filter((entry): entry is [string, string] => /^伤害值[a-z]*$/.test(entry[0]) && typeof entry[1] === 'string' && !!entry[1])
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, dice]) => {
      const suffix = key.slice('伤害值'.length);
      const skills = Object.entries(suffixMap).filter(([, value]) => value === suffix).map(([skill]) => skill);
      return [suffix ? `普攻伤害骰（${skills.join('／') || suffix}）` : '普攻伤害骰', dice];
    });
  // Cartridge attacks read these spell dice instead of the ordinary damage field.
  if (serial === 'Y14') {
    for (const name of ['像素射击', '重剑·迅捷', '重剑·勇气', '回旋踢']) {
      const dice = spells.find(spell => spell.name === name)?.effects?.[0]?.伤害骰;
      if (dice) variants.push([`普攻伤害骰（${name}）`, dice]);
    }
  }
  return variants.length ? variants : [['普攻伤害骰', '1d4']];
}
// Transformation cannot lower skills that already exceed the bonus cap.
const transformedSkill = (value: number) => Math.max(value, Math.min(value + 20, 80));

export function characterStats(attrs: Record<string, number>, serial = '', strAttrs: DamageAttrs = {}, spells: AttackSpell[] = []): Stat[] {
  const level = attrs['等级'] || 1;
  const hp = attrs['体力上限'] ?? attrs['体力'] ?? 10;
  const mp = attrs['魔力上限'] ?? attrs['魔力'] ?? 10;
  const mov = attrs['行动力'] ?? 8;
  const brawl = attrs['斗殴'] ?? 0;
  const dodge = attrs['闪避'] ?? 25;
  const build = attrs['体格'] ?? 0;
  const beforeDb = strAttrs.db || dbTable[Math.trunc(build)] || '0';
  const afterDb = dbTable[Math.trunc(build + 1)] || '0';
  const extra = attrs['额外行动数'] ?? attrs['回合行动数'] ?? 0;
  const attacks = (mov: number, transformed = false) => {
    const base = attrs['MOV'] ?? (attrs['分离行动力移动力'] ? attrs['移动力'] ?? mov : mov);
    const effectiveMov = Math.floor(Math.max(0, base + (transformed && attrs['飞行'] ? 8 : 0)));
    return attrs['无主动行动'] === 1 ? 0 : Math.max(1, Math.floor((effectiveMov + 1) / 5) + extra);
  };
  return [
    { label: '体力', before: hp, after: Math.trunc(hp * (5 + level)) },
    { label: '魔力', before: mp, after: Math.trunc(mp * (2 + 0.5 * level)) },
    { label: '行动力', before: mov, after: mov + 3 + (attrs['飞行'] ? 8 : 0) },
    { label: '斗殴', before: brawl, after: brawl > 0 ? transformedSkill(brawl) : brawl },
    { label: '闪避', before: dodge, after: transformedSkill(dodge) },
    ...['敏捷', '体质', '幸运'].map(label => ({ label, before: attrs[label], after: attrs[label] })),
    { label: 'db', before: beforeDb, after: afterDb },
    ...damageExpressions(strAttrs, serial, spells).map(([label, dice]) => ({ label, before: resolveDb(dice, beforeDb), after: resolveDb(dice, afterDb) })),
    { label: '攻击次数', before: attacks(mov), after: attacks(mov + 3, true) },
    ...[...combatSkills.filter(skill => skill !== '斗殴'), '弓术']
      .filter(skill => attrs[skill] != null)
      .map(label => ({ label, before: attrs[label], after: attrs[label] > 0 && (label !== '弓术' || serial === 'Y17') ? transformedSkill(attrs[label]) : attrs[label] })),
  ];
}
