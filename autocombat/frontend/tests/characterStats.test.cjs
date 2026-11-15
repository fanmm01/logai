const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const compiled = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../src/characterStats.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;
const loaded = { exports: {} };
new Function('module', 'exports', compiled)(loaded, loaded.exports);
const { characterStats } = loaded.exports;

test('flight adds eight only to transformed displayed action power', () => {
  const attrs = { 行动力: 8, 飞行: 1 };
  const rows = characterStats(attrs, 'Y13');
  assert.deepEqual(rows.find(s => s.label === '行动力'), { label: '行动力', before: 8, after: 19 });
  assert.equal(rows.slice(0, 3)[2].after, 19); // Top-right uses these same rows.
  assert.deepEqual(rows.find(s => s.label === '攻击次数'), { label: '攻击次数', before: 1, after: 4 });
  assert.equal(attrs['行动力'], 8);
});

test('flight affects only transformed attack counts, preserving extra actions and action restrictions', () => {
  const attacks = attrs => characterStats(attrs).find(s => s.label === '攻击次数');
  assert.deepEqual(attacks({ 行动力: 5, 飞行: 1 }), { label: '攻击次数', before: 1, after: 3 });
  assert.deepEqual(attacks({ 行动力: 8, 飞行: 1, 额外行动数: 1 }), { label: '攻击次数', before: 2, after: 5 });
  assert.deepEqual(attacks({ 行动力: 8, 飞行: 0 }), { label: '攻击次数', before: 1, after: 2 });
  assert.deepEqual(attacks({ 行动力: 8, 飞行: 1, 无主动行动: 1 }), { label: '攻击次数', before: 0, after: 0 });
});

test('ground characters gain three movement on transformation', () => {
  for (const flight of [undefined, 0]) {
    const row = characterStats({ 行动力: 8, 飞行: flight }).find(s => s.label === '行动力');
    assert.deepEqual(row, { label: '行动力', before: 8, after: 11 });
  }
});

const damageRows = (...args) => characterStats(...args).filter(row => row.label.startsWith('普攻伤害骰'));

test('basic damage resolves explicit and transformed db without mutating source data', () => {
  const strings = { db: '1d4', 伤害值: '1d8+1d3+db' };
  assert.deepEqual(damageRows({ 体格: 1 }, 'Y15', strings), [
    { label: '普攻伤害骰', before: '1d8+1d3+1d4', after: '1d8+1d3+1d6' },
  ]);
  assert.equal(strings.伤害值, '1d8+1d3+db');
});

test('all weapon damage variants resolve zero, negative and half db', () => {
  const strings = { db: '-2', damage_suffix_map: { 剑: 'a', 弓术: 'b' }, 伤害值: '1d6+db', 伤害值b: '1d6+db/2' };
  assert.deepEqual(damageRows({ 体格: 0 }, 'Y17', strings), [
    { label: '普攻伤害骰', before: '1d6-2', after: '1d6+1d4' },
    { label: '普攻伤害骰（弓术）', before: '1d6-2/2', after: '1d6+1d4/2' },
  ]);
  assert.deepEqual(damageRows({ 体格: -1 }, '', { 伤害值: '1d6+DB/2' }), [
    { label: '普攻伤害骰', before: '1d6-1/2', after: '1d6' },
  ]);
});

test('suffix-only data and serialized weapon maps preserve every damage expression', () => {
  assert.deepEqual(damageRows({}, '', { 伤害值a: '2d6', 伤害值b: '1d8+1d6+4' }), [
    { label: '普攻伤害骰（剑）', before: '2d6', after: '2d6' },
    { label: '普攻伤害骰（斗殴）', before: '1d8+1d6+4', after: '1d8+1d6+4' },
  ]);
  const row = damageRows({}, '', { damage_suffix_map: '{"弓术":"b"}', 伤害值b: '1d6+db/2' })[0];
  assert.deepEqual(row, { label: '普攻伤害骰（弓术）', before: '1d6', after: '1d6+1d4/2' });
});

test('cartridge attacks display their spell damage variants', () => {
  const spells = ['像素射击', '重剑·迅捷', '重剑·勇气', '回旋踢'].map((name, index) => ({ name, effects: [{ 伤害骰: `${index + 1}d6+db` }] }));
  const rows = damageRows({}, 'Y14', { 伤害值: '1d4+db' }, spells);
  assert.equal(rows.length, 5);
  assert.deepEqual(rows[2], { label: '普攻伤害骰（重剑·迅捷）', before: '2d6', after: '2d6+1d4' });
  assert.deepEqual(damageRows({}), [{ label: '普攻伤害骰', before: '1d4', after: '1d4' }]);
});
