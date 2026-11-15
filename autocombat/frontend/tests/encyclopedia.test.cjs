const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const ts = require('typescript');
const filename = path.resolve(__dirname, '../src/encyclopedia.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true },
}).outputText;
const loaded = { exports: {} };
new Function('require', 'module', 'exports', compiled)(createRequire(filename), loaded, loaded.exports);
const { withEncyclopedia } = loaded.exports;
const profiles = require('../src/data/encyclopedia.json');
const approvedY9Y12 = require('./fixtures/y9-y12-approved.json');
const raw = (serial, spells) => ({ serial, name: serial, attrs: {}, spells, portrait: '', avatar: '' });

// Approved by the user on 2026-11-16 before updating the page or fixtures.
// This independent field-level baseline supersedes the earlier wording.
const approvedStage3 = require('./fixtures/stage3-approved.json');
test('stage-three approved fields remain exact after spell reordering and combat edits', () => {
  let count = 0;
  for (const [serial, approved] of Object.entries(approvedStage3)) {
    const input = raw(serial, profiles[serial].skills.map(({ name, phase }) => ({
      name, phase, 消耗mp: 999, effects: [{ type: 1, 伤害骰: '9d99' }],
    })).reverse());
    const original = structuredClone(input);
    const actual = withEncyclopedia(input);
    if (approved.passives) assert.deepEqual(actual.passives, approved.passives);
    for (const patch of approved.skills || []) {
      const article = actual.skills.find(a => a.name === patch.name && a.phase === patch.phase);
      assert.ok(article, `${serial}: ${patch.name}`);
      for (const [key, value] of Object.entries(patch)) {
        assert.deepEqual(article[key], value, `${serial}/${patch.name}: ${key}`);
      }
      count++;
    }
    assert.deepEqual(input, original);
  }
  assert.equal(count, 9);
});

test('all 106 frontend articles survive reordering and phase matching', () => {
  let count = 0;
  for (const [serial, profile] of Object.entries(profiles)) {
    const spells = profile.skills.map(({ name, phase }) => ({ name, phase })).reverse();
    const actual = withEncyclopedia(raw(serial, spells));
    assert.deepEqual(actual.skills.map(s => s.id), profile.skills.map(s => s.id).reverse());
    for (const skill of actual.skills) {
      assert.ok(skill.description);
      assert.ok(fs.existsSync(path.resolve(__dirname, '../public', skill.icon.replace('/ui/', ''))));
    }
    count += actual.skills.length;
  }
  assert.equal(count, 106);
});

// Historical prose is retained as an independent baseline for identities and size.
test('Y9–Y12 cover all 18 combat skill identities with the same identities and shorter text', () => {
  const expectedKeys = {
    Y9: [['召唤生灵', 1], ['生之矛', 1], ['领域·生', 1], ['阶段转换', 1], ['召唤生灵', 2], ['死之矛', 2], ['领域·灭', 2]],
    Y10: [['屏障术', 0], ['治疗术', 0], ['藤蔓术', 0], ['召唤蜂鸟', 0]],
    Y11: [['挥动镰刀', 0], ['黑雾笼罩', 0], ['镰刃余势', 0]],
    Y12: [['空间扭曲', 0], ['空间压缩', 0], ['空间扩张', 0], ['空间引入', 0]],
  };
  for (const [serial, keys] of Object.entries(expectedKeys)) {
    assert.deepEqual(profiles[serial].skills.map(s => [s.name, s.phase]), keys);
    assert.deepEqual(approvedY9Y12[serial].skills.map(s => [s.name, s.phase]), keys);
    const actualText = profiles[serial].skills.map(({ name, phase, timing, cost, limits, description, facts }) =>
      ({ name, phase, timing, cost, limits, description, facts }));
    assert.deepEqual(actualText.map(({ name, phase, timing }) => ({ name, phase, timing })),
      approvedY9Y12[serial].skills.map(({ name, phase, timing }) => ({ name, phase, timing })));
    assert.ok(JSON.stringify(actualText).length <= JSON.stringify(approvedY9Y12[serial].skills).length);
  }
  const trait = approvedY9Y12.Y9.trait;
  assert.deepEqual(profiles.Y9.traits.find(t => t.name === trait.name), trait);
});

test('Y9–Y12 keep approved articles through combat changes, reordering and omitted phase zero', () => {
  for (const serial of Object.keys(approvedY9Y12)) {
    const articles = profiles[serial].skills;
    const spells = articles.map(({ name, phase }, i) => ({
      name, ...(phase ? { phase } : {}), timing: '4', 消耗mp: 100 + i,
      effects: [{ type: 1, 伤害骰: '9d99', 成功率: 1 }],
    })).reverse();
    const input = raw(serial, spells);
    const before = structuredClone(input);
    const actual = withEncyclopedia(input);
    assert.deepEqual(actual.skills, [...articles].reverse());
    assert.deepEqual(actual.spells, spells);
    assert.deepEqual(input, before);
    for (const skill of articles) {
      assert.match(withEncyclopedia(raw(serial, [{ name: skill.name, phase: 99 }])).skills[0].description, /尚未编写/);
    }
  }
  const phases = withEncyclopedia(raw('Y9', [{ name: '召唤生灵', phase: 2 }, { name: '召唤生灵', phase: 1 }])).skills;
  assert.match(phases[0].limits, /木·枯/);
  assert.match(phases[1].limits, /木·荣/);
  assert.notEqual(phases[0].description, phases[1].description);
});

test('Y9–Y12 retain skill identities and icons after the global copy revision', () => {
  const { createHash } = require('node:crypto');
  const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
  const serials = Object.keys(approvedY9Y12);
  const identities = serials.map(serial => [serial, profiles[serial].skills.map(({ id, name, phase, icon }) => ({ id, name, phase, icon }))]);
  assert.equal(hash(identities), '98d374e29c67385cbc851d4a4ee87a6a262043998dd9277b609b37dd59a6900b');
});

// Transcribed from the approved review, never regenerated from runtime prose.
const approvedY17Y60 = require('./fixtures/y17-y60-approved.json');

test('Y17/Y60 player text keeps reviewed identities with shorter text', () => {
  for (const [serial, approved] of Object.entries(approvedY17Y60)) {
    const actual = profiles[serial].skills.map(({ name, phase, timing, cost, limits, description, facts }) =>
      ({ name, phase, timing, cost, limits, description, facts }));
    assert.deepEqual(actual.map(({ name, phase, timing }) => ({ name, phase, timing })),
      approved.skills.map(({ name, phase, timing }) => ({ name, phase, timing })));
    assert.ok(JSON.stringify(actual).length < JSON.stringify(approved.skills).length);
  }
});

test('Y17/Y60 retain all 15 identities through reordering, omitted phase zero and combat changes', () => {
  let count = 0;
  for (const serial of Object.keys(approvedY17Y60)) {
    const articles = profiles[serial].skills;
    const spells = articles.map(({ name, phase }) => ({
      name, ...(phase ? { phase } : {}), timing: '4', 消耗mp: 999,
      effects: [{ type: 1, 伤害骰: '9d99', 成功率: 1 }],
    })).reverse();
    const input = raw(serial, spells);
    const before = structuredClone(input);
    assert.deepEqual(withEncyclopedia(input).skills, [...articles].reverse());
    assert.deepEqual(input, before);
    for (const skill of articles) {
      const phase = skill.phase === 99 ? 0 : 99;
      assert.match(withEncyclopedia(raw(serial, [{ name: skill.name, phase }])).skills[0].description, /尚未编写/);
    }
    count += articles.length;
  }
  assert.equal(count, 15);
});

test('Y17/Y60 retain skill identities and icons after the global copy revision', () => {
  const { createHash } = require('node:crypto');
  const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
  const serials = ['Y17', 'Y60'];
  assert.equal(hash(serials.map(serial => [serial, profiles[serial].skills.map(({ id, name, phase, icon }) =>
    ({ id, name, phase, icon }))])), 'b1002a3998286dc6531e4e8ee00f10954ace42b64251a3b1c28bce14915c8b86');
});

test('Y17/Y60 static prose covers real character data and pins its documented rule values', () => {
  const { execFileSync } = require('node:child_process');
  const script = `
import ast, json
from pathlib import Path
cards = {}
for node in ast.parse(Path('characters_data.py').read_text(encoding='utf-8')).body:
    if not isinstance(node, ast.Assign): continue
    try: value = ast.literal_eval(node.value)
    except (ValueError, TypeError): continue
    if isinstance(value, dict) and value.get('serial') in ('Y17', 'Y60'):
        cards[value['serial']] = value
print(json.dumps(cards, ensure_ascii=False))
`;
  const cards = JSON.parse(execFileSync('python', ['-X', 'utf8', '-c', script], {
    cwd: path.resolve(__dirname, '../..'), encoding: 'utf8',
  }));
  for (const [serial, card] of Object.entries(cards)) {
    assert.deepEqual(profiles[serial].skills.map(s => [s.name, s.phase]),
      card.spells.map(s => [s.name, s.phase ?? 0]));
  }
  assert.deepEqual(Object.keys(cards).sort(), ['Y17', 'Y60']);
  const blood = cards.Y17;
  assert.equal(blood.初始血液, 2000);
  assert.deepEqual(blood.blood_rules, {
    moon_phases: [['满月', '1d12+3', true], ['亏凸月', '1d8', true], ['弦月', '1d12', false],
      ['残月', '1d6+4', false], ['新月', '1d16+12', false], ['蛾眉月', '8', false],
      ['上弦月', '5', true], ['盈凸月', '10', true]],
    arcana_base_blood: 100, arcana_bonus_dice: 1, arcana_substitute_ml: 500,
    armor_cap: 12, armor_ml: 300, armor_per_unit: 2, ball_heal: 4, ball_ml: 500,
    blade_damage: '1d8+2', blade_ml: 100, bow_damage: '1d8+2', bow_range: 8,
    cape_ml: 1000, cape_mp: 4, chain_ml: 3000, corpse_ml: 300, duration: 10,
    heal_max_units: 4, hp_to_ml: 200, slash_base_ml: 500, slash_extra_ml: 1000,
    slash_harvest_cap: 200, slash_harvest_ml_per_hp: 25, source_cap: 1000,
    spear_cap: 2, spear_melee: '1d8+6', spear_ml: 1000, spear_range: 8,
    spear_throw: '1d10+6', spear_uses: 3, war_extra_radius: 5, war_radius: 10,
  });
  const spell = (card, name) => card.spells.find(s => s.name === name);
  assert.equal(spell(blood, '阿瑞卡纳律动').吟唱回合, 3);
  assert.equal(spell(blood, '祭炼式-破').吟唱回合, 2);
  assert.equal(spell(blood, '祭炼式-破').chant_interrupt_hp_pct, 90);
  assert.equal(spell(blood, '祭炼式-破').chant_interrupt_mp_pct, 50);
  assert.equal(spell(blood, '污血战争').cost_mp_pct_max, 20);
  assert.equal(spell(blood, '污血战争').cost_hp_dice, '1d8');
  assert.equal(spell(blood, '污血战争').消耗san, '1d8');
  assert.equal(spell(blood, '血月斩击').消耗mp, '1d8');
  const snow = cards.Y60;
  assert.deepEqual(snow.spells.map(s => s.消耗mp), [4, 2, 2]);
  assert.deepEqual(spell(snow, '制造雪带').effects, [
    { type: 8, 作用半径: 5, 客体: 45, 属性削减: '行动力-5;斗殴-10', 持续回合: 3,
      每回合伤害骰: '1d6', 领域中心跟随: 0, 领域同时上限: 1 },
    { type: 1, 伤害骰: '1d6+1d4', 可反击性: 0, 可闪避性: 1, 客体: 45, 射程: 99, 成功率: 70 },
  ]);
  const curtain = spell(snow, '雪幕').effects[0];
  assert.equal(curtain.作用半径, 5);
  assert.equal(curtain.来源存活绑定, 1);
  assert.equal(curtain.领域中心跟随, 1);
  assert.equal(curtain.辅助效果值, '-10');
  assert.deepEqual(spell(snow, '发送雪球').effects[0],
    { type: 1, 伤害骰: '1d6+1d4+2', 可反击性: 0, 可闪避性: 1, 客体: 4, 射程: 99, 成功率: 75 });
});

test('unknown characters and phases get an explicit missing article', () => {
  const spell = { name: 'custom', phase: 99 };
  assert.match(withEncyclopedia(raw('CUSTOM', [spell])).skills[0].description, /尚未编写/);
  const existing = profiles.Y9.skills[0];
  assert.match(withEncyclopedia(raw('Y9', [{ name: existing.name, phase: 123 }])).skills[0].description, /尚未编写/);
});

test('frontend prose overrides legacy server text without mutating combat data', () => {
  const first = profiles.Y1.skills[0];
  const card = { ...raw('Y1', [{ name: first.name, phase: first.phase }]), skills: [], basic: 'server prose' };
  const original = structuredClone(card);
  const actual = withEncyclopedia(card);
  assert.equal(actual.basic, profiles.Y1.basic);
  assert.deepEqual(actual.skills, [first]);
  assert.deepEqual(card, original);
});

test('Y5 and Y6 keep approved articles when combat fields change or spells reorder', () => {
  for (const serial of ['Y5', 'Y6']) {
    const profile = profiles[serial];
    const spells = profile.skills.map(({ name, phase }, i) => ({
      name, phase, timing: '2', 消耗mp: i + 20, custom: 123,
      effects: [{ type: 1, 伤害骰: '9d99', unknown: true }],
    })).reverse();
    const card = raw(serial, spells);
    const before = structuredClone(card);
    const actual = withEncyclopedia(card);
    assert.deepEqual(actual.skills, [...profile.skills].reverse());
    assert.deepEqual(actual.passives, profile.passives);
    assert.deepEqual(actual.spells, spells);
    assert.deepEqual(card, before);
    for (const article of actual.skills) {
      assert.ok(!article.facts.includes('施放要求'));
      assert.ok(!article.facts.some(line => line.startsWith('基础费用：')));
    }
    const otherPhase = { name: profile.skills[0].name, phase: 99 };
    assert.match(withEncyclopedia(raw(serial, [otherPhase])).skills[0].description, /尚未编写/);
  }
});

// Reviewed player text is an independent fixture; combat fields must never
// regenerate it or affect its name/phase association.
const approvedY13Y16 = require('./fixtures/y13-y16-approved.json');

test('Y13-Y16 retain skill identities and icons after the global copy revision', () => {
  const { createHash } = require('node:crypto');
  const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
  const serials = Object.keys(approvedY13Y16);
  assert.equal(hash(serials.map(serial => [serial, profiles[serial].skills.map(({ id, name, phase, icon, reviewed }) =>
    ({ id, name, phase, icon, reviewed }))])), '32a1420754e770d8523dd4610dd3593b2a6536a135b8dbc39f49c99aabae2495');
});

test('all 47 Y13-Y16 articles keep identities and shorten historical prose', () => {
  let count = 0;
  for (const [serial, rows] of Object.entries(approvedY13Y16)) {
    const expected = rows.map(([name, phase, timing, cost, limits, description, ...facts]) =>
      ({ name, phase, timing, cost, limits, description, facts }));
    assert.deepEqual(profiles[serial].skills.map(({ name, phase, timing }) => ({ name, phase, timing })),
      expected.map(({ name, phase, timing }) => ({ name, phase, timing })), serial);
    assert.ok(JSON.stringify(profiles[serial].skills.map(({ id, icon, reviewed, ...rest }) => rest)).length
      < JSON.stringify(expected).length, `${serial}: concise copy`);
    count += rows.length;
  }
  assert.equal(count, 47);
});

test('Y13-Y16 match by identity after reordering and ignore legacy generated prose', () => {
  for (const [serial, profile] of Object.entries(profiles).filter(([key]) => key in approvedY13Y16)) {
    const spells = profile.skills.map(({ name, phase }, i) => ({
      name, phase, timing: '2', 消耗mp: i + 30,
      effects: [{ type: 1, 伤害骰: '9d99' }], description: 'legacy server text',
    })).reverse();
    const card = raw(serial, spells);
    const before = structuredClone(card);
    const actual = withEncyclopedia(card);
    assert.deepEqual(actual.skills, [...profile.skills].reverse());
    assert.deepEqual(card, before);
    for (const article of actual.skills) {
      assert.equal(article.id, JSON.stringify([serial, article.name, article.phase]));
      assert.ok(fs.existsSync(path.resolve(__dirname, '../public', article.icon.replace('/ui/', ''))));
      const wrongPhase = { name: article.name, phase: article.phase === 99 ? 0 : 99 };
      assert.match(withEncyclopedia(raw(serial, [wrongPhase])).skills[0].description, /尚未编写/);
    }
  }
});
