const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { loadTs } = require('./load-ts.cjs');
const templates = require('../../frontend/src/data/encyclopedia.json');
const { cards, summons, items, rawCatalog, catalog, profiles } = require('./catalog-fixture.cjs');
const { resolveEncyclopediaValues } = loadTs(path.resolve(__dirname, '../../frontend/src/encyclopediaValues.ts'));
const { EncyclopediaSkill } = loadTs(path.resolve(__dirname, '../../frontend/src/EncyclopediaSkill.tsx'));
const { buildDefinitions } = loadTs(path.resolve(__dirname, '../../frontend/src/knowledge.ts'));
const { withEncyclopedia } = loadTs(path.resolve(__dirname, '../../frontend/src/encyclopedia.ts'));
const article = (card, spell) => profiles[card.serial].skills.find(s => s.name === spell.name && s.phase === (spell.phase ?? 0));
const prose = skill => [skill.cost, skill.limits, skill.description, ...skill.facts].join('\n');
const plain = html => html.replace(/<[^>]*>/g, '');

test('all 106 articles contain current costs, damage, healing and summon quantities', () => {
  let count = 0;
  for (const card of cards) {
    assert.ok(profiles[card.serial].basic.includes(`普通攻击基础骰：${card.str_attrs?.['伤害值'] ?? '未单独设定，依引擎默认规则'}；`));
    for (const spell of card.spells) {
      const text = prose(article(card, spell));
      const label = `${card.serial}/${spell.name}`;
      assert.ok(text.includes(`${spell['消耗mp']} MP`) || spell._mp_formula || spell.blood_kind, `${label}: fee`);
      for (const effect of spell.effects) {
        for (const key of ['伤害骰', '护盾值', '回复hp', '回复mp', '回复san', '每回合伤害骰', '制造个数', '召唤个数']) {
          if (effect.consume_moon || ['arcana', 'sacrifice'].includes(spell.blood_kind)) continue;
          const value = effect[key];
          if (value != null) assert.ok(text.includes(String(value)), `${label}: ${key}=${value}`);
        }
        if (effect['辅助效果'] === '伤害骰加值') {
          assert.ok(text.includes(effect['辅助效果值'].replace(/^\+/, '')), `${label}: enchantment`);
        }
      }
      count++;
    }
  }
  assert.equal(count, 106);
  const ember = profiles.Y15.skills.find(s => s.name === '余烬');
  assert.match(ember.description, /连续发动两段/);
  assert.equal(cards.find(c => c.serial === 'Y15').spells.find(s => s.name === '余烬').effects.length, 2);
});

test('long-range attack prose does not expose the global override value or a substitute phrase', () => {
  const entries = [
    ['Y1', '火焰附魔'], ['Y2', '火球术'], ['Y4', '星闪攻击'],
    ['Y5', '吸血'], ['Y6', '发送雪球'], ['Y10', '藤蔓术'],
    ['Y14', '光速救援'], ['Y16', '万众瞩目的乌托邦'],
    ['Y60', '制造雪带'], ['Y60', '发送雪球'],
  ];
  for (const [serial, name] of entries) {
    const skill = profiles[serial].skills.find(item => item.name === name);
    const text = prose(skill);
    assert.doesNotMatch(text, /99\s*格|较远/, `${serial}/${name}`);
  }
});

test('current combat data never rewrites unrelated prose, summon stats, db or resource formulas', () => {
  for (const character of cards) {
    for (const spell of character.spells) {
      const summonName = spell.effects.map(e => e['召唤物模板']).find(Boolean);
      const data = { character: { ...character, summon_templates: summons, item_templates: items }, spell, summonTemplate: summons[summonName] };
      const skill = article(character, spell);
      for (const line of [skill.description, ...skill.facts, skill.limits]) {
        assert.equal(resolveEncyclopediaValues(line, data), line, `${character.serial}/${spell.name}: ${line}`);
      }
      const text = plain(renderToStaticMarkup(React.createElement(EncyclopediaSkill, { skill, data })));
      assert.ok(text.includes(skill.description), `${character.serial}/${spell.name}: rendered description`);
    }
  }
});

test('summon sections keep each template stats and distinguish free attacks from paid spells', () => {
  const skill = profiles.Y12.skills.find(s => s.name === '空间引入');
  for (const name of ['玄武', '朱雀', '龙虎']) {
    const start = skill.facts.indexOf(`### ${name}`) + 1;
    const end = skill.facts.findIndex((line, i) => i >= start && line.startsWith('### '));
    const lines = skill.facts.slice(start, end);
    for (const [label, key] of [['生命', 'HP'], ['基础行动力', 'MOV'], ['敏捷', 'DEX'], ['闪避', '闪避']]) {
      assert.ok(lines.includes(`${label}：${summons[name][key]}`), `${name}/${label}`);
    }
  }
  assert.ok(skill.facts.includes(`盾牌耐久：${summons['玄武'].shield_block}`));
  assert.match(prose(skill), /攻击招式时，费用分别为 0 MP、0 MP/);
  assert.match(prose(skill), /主动施放盾牌冲撞时，费用为 1 MP/);
});

test('definitions and skill panels share current damage and magic reduction wording', () => {
  const definitions = buildDefinitions(catalog);
  const get = (serial, name) => definitions.find(d => d.serial === serial && d.name === name).text;
  assert.match(get('Y15', '余烬'), /两段.*2d6\+1/);
  assert.match(get('Y16', '幻造兵武'), /附加 1d4/);
  assert.match(get('Y13', '魔力永驻'), /最低为 0 MP/);
  assert.doesNotMatch(get('Y13', '魔力永驻'), /仍至少消耗 1 MP/);
});

test('zero base costs and composite trigger costs remain distinct', () => {
  const character = { attrs: {}, spells: [] };
  const base = { id: 'test', name: 'test', description: '', facts: [], limits: '' };
  const render = (cost, spell) => plain(renderToStaticMarkup(React.createElement(EncyclopediaSkill, { skill: { ...base, cost }, data: { character, spell } })));
  assert.match(render('{{spell.消耗mp}} MP', { 消耗mp: 0 }), /0 MP/);
  assert.match(render('开战 1 MP；每次授予庇佑消耗 5 点月能', { 消耗mp: 2 }), /开战 1 MP；每次授予庇佑消耗 5 点月能/);
  assert.match(render('变身前 3d4 MP；变身后 2d3 MP', { 消耗mp: '3d4' }), /变身前 3d4 MP；变身后 2d3 MP/);
  assert.match(render('{{spell.消耗mp}} MP', { 消耗mp: '1d3+1' }), /1d3\+1 MP/);
});

test('explicit references preserve full dice expressions and unresolved paths', () => {
  const data = { character: {}, spell: { effects: [{ 伤害骰: '1d8+2+db' }] }, summonTemplate: { HP: 101 } };
  assert.equal(resolveEncyclopediaValues('伤害 {{spell.effects.0.伤害骰}}；生命 {{summonTemplate.HP}}', data), '伤害 1d8+2+db；生命 101');
  assert.equal(resolveEncyclopediaValues('{{spell.missing}}', data), '未配置');
});

test('every published reference resolves from complete character data without placeholders', () => {
  let references = 0;
  for (const character of rawCatalog) {
    const profile = templates[character.serial];
    const groups = [[{}, [profile.basic, ...profile.passives, ...profile.traits.map(t => t.description)]],
      ...character.spells.map(spell => [spell, Object.values(profile.skills.find(s => s.name === spell.name && s.phase === (spell.phase ?? 0))).flat().filter(s => typeof s === 'string')])];
    for (const [spell, lines] of groups) {
      for (const line of lines) {
        const data = { character, spell };
        for (const [token] of line.matchAll(/\{\{[^{}]+\}\}/g)) {
          const value = resolveEncyclopediaValues(token, data);
          assert.doesNotMatch(value, /未配置|\{\{/, `${character.serial}/${spell.name ?? 'profile'}: ${token}`);
          references++;
        }
      }
    }
    assert.doesNotMatch(JSON.stringify(withEncyclopedia(character)), /未配置|\{\{/);
  }
  assert.ok(references > 750);
});

test('cost edits including zero update every nonformula fee and leave source data unchanged', () => {
  let checked = 0;
  for (const input of rawCatalog) {
    for (let i = 0; i < input.spells.length; i++) {
      const spell = input.spells[i];
      if (spell._mp_formula || spell.blood_kind === 'arcana' || spell.blood_kind === 'war') continue;
      for (const cost of [0, 27]) {
        const edited = structuredClone(input);
        edited.spells[i]['消耗mp'] = cost;
        const before = structuredClone(edited);
        assert.ok(withEncyclopedia(edited).skills[i].cost.includes(`${cost} MP`), `${input.serial}/${spell.name}: ${cost}`);
        assert.deepEqual(edited, before);
      }
      checked++;
    }
  }
  assert.ok(checked > 90);
});

test('every referenced damage, healing, shield and summon quantity reacts to data edits', () => {
  let checked = 0;
  for (const input of rawCatalog) {
    for (let i = 0; i < input.spells.length; i++) {
      const spell = input.spells[i];
      const template = templates[input.serial].skills.find(s => s.name === spell.name && s.phase === (spell.phase ?? 0));
      const source = prose(template);
      for (let index = 0; index < spell.effects.length; index++) {
        for (const key of ['伤害骰', '护盾值', '回复hp', '回复mp', '回复san', '每回合伤害骰', '召唤个数', '制造个数']) {
          if (!(key in spell.effects[index])) continue;
          if (!source.includes(`spell.effects.${index}.${key}`) && !source.includes(`spell.effects.*.${key}`)) continue;
          const edited = structuredClone(input);
          edited.spells[i].effects[index][key] = '7d13+9';
          const current = withEncyclopedia(edited).skills[i];
          assert.ok(prose(current).includes('7d13+9'), `${input.serial}/${spell.name}/${index}/${key}`);
          checked++;
        }
      }
    }
  }
  assert.equal(checked, 72);
});

test('summon, cake, passive, cross-skill and blood references follow edits and skill reordering', () => {
  const card = serial => structuredClone(rawCatalog.find(c => c.serial === serial));
  const find = (c, name) => c.spells.find(s => s.name === name);
  const text = (c, name) => prose(withEncyclopedia(c).skills.find(s => s.name === name));
  const mage = card('Y2');
  mage.summon_templates['使魔'].skills = ['斗殴:87 3d9+4'];
  mage.summon_templates['使魔'].HP = 71;
  assert.match(text(mage, '召唤使魔'), /生命：71[\s\S]*斗殴：87[\s\S]*3d9\+4/);
  const snow = card('Y6');
  snow.summon_templates['小雪人'].frost_ball.mp_cost = 7;
  snow.summon_templates['小雪人'].frost_zone.duration = 11;
  snow.summon_templates['小雪人'].frost_melee = { success: 83, dice: '3d7' };
  snow.passive_rules.frost.dot = '3d13';
  snow.passive_rules.frost.vulnerability_pct = 37;
  snow.attrs['冰冻即时回复层数'] = 6;
  snow.attrs['冰冻跨循环叠加'] = 0;
  assert.match(text(snow, '堆雪人'), /雪球：消耗 7 MP/);
  assert.match(text(snow, '堆雪人'), /持续 11 轮/);
  assert.match(text(snow, '堆雪人'), /近战攻击：斗殴 83；伤害 3d7/);
  assert.match(withEncyclopedia(snow).passives.join('\n'), /37%[\s\S]*3d13/);
  assert.match(withEncyclopedia(snow).passives.join('\n'), /第 3 层：伤害降低/);
  assert.match(withEncyclopedia(snow).passives.join('\n'), /每循环第 6 层，主人立即回复/);
  assert.match(withEncyclopedia(snow).passives.join('\n'), /跨循环叠加：关闭/);
  const cake = card('Y8');
  cake.item_templates['蛋糕HP']['回复hp'] = '4d9+2';
  find(cake, '美味的蛋糕').effects[0]['制造个数'] = '3d7';
  cake.inventory[0]['消费追加回复hp'] = '8d3';
  find(cake, '最后的款待-恢复').effects[0]['消费追加回复hp'] = '9d7';
  assert.match(text(cake, '美味的蛋糕'), /制造 3d7[\s\S]*回复 4d9\+2 HP/);
  assert.match(text(cake, '最后的款待-恢复'), /回复 4d9\+2 HP/);
  assert.match(text(cake, '品尝蛋糕'), /回复 8d3 HP[\s\S]*额外回复 9d7 HP/);
  const portal = card('Y12');
  portal.summon_templates['玄武'].HP = 137;
  portal.summon_templates['朱雀'].skills.reverse();
  portal.summon_templates['朱雀'].skills.find(s => s.name === '爪击').dice = '5d7';
  portal.summon_templates['龙虎'].spells.reverse();
  assert.match(text(portal, '空间引入'), /生命：137/);
  assert.match(text(portal, '空间引入'), /爪击以 75 的技能值检定，造成 5d7/);
  assert.match(text(portal, '空间引入'), /治愈领域消耗自身 1 MP，冷却 3 轮，半径 8 格/);
  const writer = card('Y16');
  find(writer, '幻造兵武').effects[0]['辅助效果值'] = '4d7';
  writer.passive_rules.phantom['-2'] = '-3d9';
  writer.passive_rules.moon.ultimate_threshold = 49;
  writer.spells.reverse();
  assert.match(text(writer, '万众瞩目的乌托邦'), /「幻造兵武」的 4d7/);
  assert.match(text(writer, '幻造兵武'), /大失败 −3d9/);
  assert.match(text(writer, '切换·剑'), /超过 49 点/);
  const giant = card('Y13');
  find(giant, '魔力永驻').effects[0]['辅助效果值'] = '-50';
  find(giant, '飞行')['消耗mp'] = 8;
  assert.match(text(giant, '飞行'), /基础 8 MP；魔力永驻生效后为 4 MP/);
  const blood = card('Y17');
  blood.blood_rules.moon_phases[0][1] = '9d11';
  blood.blood_rules.armor_cap = 18;
  blood.blood_rules.spear_throw = '6d9';
  assert.match(text(blood, '血月斩击'), /满月：基础伤害 9d11/);
  assert.match(text(blood, '凝血式-守'), /300～2700 ml[\s\S]*最多减少 18/);
  assert.match(text(blood, '血色突刺'), /伤害 6d9/);
});

test('missing paths and invalid formatting stay explicit and never execute article text', () => {
  const data = { character: { attrs: {} }, spell: { effects: [{ 伤害骰: '1d8+db' }, {}] } };
  assert.equal(resolveEncyclopediaValues('{{spell.effects.*.伤害骰|join:、}}', data), '未配置');
  assert.equal(resolveEncyclopediaValues('{{spell.effects.0.伤害骰|unknown}}', data), '未配置');
  assert.equal(resolveEncyclopediaValues('{{spell.__proto__.polluted}}', data), '未配置');
  assert.equal(resolveEncyclopediaValues('固定规则 D100；1 HP；普通近战 2 格', data), '固定规则 D100；1 HP；普通近战 2 格');
});
