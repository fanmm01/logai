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
