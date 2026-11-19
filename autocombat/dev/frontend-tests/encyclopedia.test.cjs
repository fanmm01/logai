const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { loadTs } = require('./load-ts.cjs');
const { withEncyclopedia } = loadTs(path.resolve(__dirname, '../../frontend/src/encyclopedia.ts'));
const templates = require('../../frontend/src/data/encyclopedia.json');
const { rawCatalog, catalog, profiles } = require('./catalog-fixture.cjs');
const identity = ({ id, name, phase, icon }) => ({ id, name, phase, icon });
const historical = [require('./fixtures/y9-y12-approved.json'), require('./fixtures/y17-y60-approved.json')];

test('all 106 articles bind by name and phase after reordering without mutating data', () => {
  let count = 0;
  for (const input of rawCatalog) {
    const reversed = structuredClone(input);
    reversed.spells.reverse();
    const before = structuredClone(reversed);
    const actual = withEncyclopedia(reversed);
    assert.deepEqual(actual.skills, [...profiles[input.serial].skills].reverse());
    assert.deepEqual(reversed, before);
    for (const skill of actual.skills) {
      assert.ok(skill.description);
      assert.ok(fs.existsSync(path.resolve(__dirname, '../../frontend/public', skill.icon.replace('/ui/', ''))));
    }
    count += actual.skills.length;
  }
  assert.equal(count, 106);
});

test('historical skill identities survive the data-reference migration', () => {
  for (const group of historical) {
    for (const [serial, approved] of Object.entries(group)) {
      assert.deepEqual(profiles[serial].skills.map(s => [s.name, s.phase]), approved.skills.map(s => [s.name, s.phase]));
    }
  }
  const stages = require('./fixtures/y13-y16-approved.json');
  for (const [serial, rows] of Object.entries(stages)) {
    assert.deepEqual(profiles[serial].skills.map(s => [s.name, s.phase]), rows.map(([name, phase]) => [name, phase]));
  }
  for (const c of catalog) assert.deepEqual(c.skills.map(identity), templates[c.serial].skills.map(identity));
});

test('same-name phases retain their own effect data and phase-specific prose', () => {
  const card = structuredClone(rawCatalog.find(c => c.serial === 'Y9'));
  const first = card.spells.find(s => s.name === '召唤生灵' && s.phase === 1);
  const second = card.spells.find(s => s.name === '召唤生灵' && s.phase === 2);
  first.effects[0]['召唤个数'] = '2d7';
  second.effects[0]['召唤个数'] = '3d9';
  card.spells.reverse();
  const actual = withEncyclopedia(card);
  assert.match(actual.skills.find(s => s.name === first.name && s.phase === 1).description, /召唤 2d7/);
  assert.match(actual.skills.find(s => s.name === second.name && s.phase === 2).description, /召唤 3d9/);
  assert.match(actual.skills.find(s => s.name === first.name && s.phase === 1).limits, /木·荣/);
  assert.match(actual.skills.find(s => s.name === second.name && s.phase === 2).limits, /木·枯/);
});

test('unknown characters and missing phases have an explicit missing article', () => {
  for (const card of catalog) {
    const spell = card.spells[0];
    const phase = spell.phase === 99 ? 123 : 99;
    assert.match(withEncyclopedia({ ...card, spells: [{ ...spell, phase }] }).skills[0].description, /尚未编写/);
  }
  assert.match(withEncyclopedia({ serial: 'CUSTOM', name: 'custom', attrs: {}, spells: [{ name: 'custom' }] }).skills[0].description, /尚未编写/);
});

test('frontend references override legacy prose while timing follows current data', () => {
  const card = structuredClone(rawCatalog.find(c => c.serial === 'Y1'));
  card.skills = []; card.basic = 'legacy server text';
  card.spells[0].timing = '23'; card.spells[0]['消耗mp'] = 17;
  const actual = withEncyclopedia(card);
  assert.equal(actual.basic, profiles.Y1.basic);
  assert.equal(actual.skills[0].cost, '17 MP');
  assert.equal(actual.skills[0].timing, '主动／附加动作');
  assert.deepEqual(actual.skills.map(identity), profiles.Y1.skills.map(identity));
});

test('incomplete custom data shows missing configuration without leaking template syntax', () => {
  const c = rawCatalog.find(c => c.serial === 'Y16');
  const result = withEncyclopedia({ serial: c.serial, name: c.name, attrs: {}, spells: c.spells.map(s => ({ name: s.name })) });
  assert.doesNotMatch(JSON.stringify(result.skills), /\{\{/);
  assert.match(JSON.stringify(result.skills), /未配置/);
});
