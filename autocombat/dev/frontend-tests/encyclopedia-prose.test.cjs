const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { loadTs } = require('./load-ts.cjs');
const loaded = { exports: loadTs(path.resolve(__dirname, '../../frontend/src/EncyclopediaProse.tsx')) };
const render = (content, data) => renderToStaticMarkup(React.createElement(loaded.exports.EncyclopediaProse, { content, data })).replace(/ data-definition-source="[^"]*"/g, '');

test('explicit skill references render cost, range, check and each hit from current data', () => {
  const { Y2 } = require('../../frontend/src/data/encyclopedia.json');
  const skill = { ...Y2.skills.find(item => item.name === '火球术'),
    limits: '敌方单体｜射程 {{spell.effects.0.射程}} 格',
    description: '每枚分别以 {{spell.effects.0.成功率}} 的技能值检定，依次造成 {{spell.effects.0.伤害骰}}、{{spell.effects.1.伤害骰}}、{{spell.effects.2.伤害骰}} 伤害。',
  };
  const character = { attrs: {}, str_attrs: {}, spells: [] };
  const spell = { 消耗mp: 7, effects: [
    { 射程: 16, 成功率: 82, 伤害骰: '2d6' },
    { 伤害骰: '3d4+1' },
    { 伤害骰: '1d10+3' },
  ] };
  const component = loadTs(path.resolve(__dirname, '../../frontend/src/EncyclopediaSkill.tsx')).EncyclopediaSkill;
  const html = renderToStaticMarkup(React.createElement(component, { skill, data: { character, spell } }));
  assert.match(html, /7 MP/);
  assert.match(html, /射程 16 格/);
  assert.match(html, /82 的技能值检定/);
  assert.match(html, /2d6、3d4\+1、1d10\+3 伤害/);
});

test('existing snowman rules become headings and a contiguous eight-level list without losing text', () => {
  const { profiles: { Y6 } } = require('./catalog-fixture.cjs');
  const html = render(Y6.passives);
  assert.match(html, /<h5>八层循环<\/h5><ul class="prose-levels">/);
  assert.equal((html.match(/<li>/g) || []).length, 8);
  assert.match(html, /<\/ul><h5>循环与衰减<\/h5>/);
  for (const line of Y6.passives) {
    assert.ok(html.replace(/<[^>]+>/g, '').includes(line.replace(/^#{1,6}\s+/, '')), line);
  }
});

test('skill facts distinguish requirements, parameter labels and effects', () => {
  const html = render(['施放要求', '基础费用：4 MP', '技能效果', '造成伤害。']);
  assert.match(html, /<h5>施放要求<\/h5><table/);
  assert.match(html, /<th scope="row">基础费用<\/th><td>4 MP<\/td>/);
  assert.match(html, /<\/table><h5>技能效果<\/h5><p>造成伤害。<\/p>/);
});

test('explicit encyclopedia value references render from the matched character and spell data', () => {
  const character = { attrs: { 体力: 37 } };
  const spell = { 消耗mp: 6, effects: [{ 射程: 14, 伤害骰: '3d8+2' }] };
  const html = render([
    '射程：{{spell.effects.0.射程}} 格',
    '伤害：{{spell.effects.0.伤害骰}}',
    '费用：{{spell.消耗mp}} MP；生命上限 {{character.attrs.体力}}',
  ], { character, spell });
  assert.match(html, /<td>14 格<\/td>/);
  assert.match(html, /<th scope="row">伤害<\/th><td>3d8\+2<\/td>/);
  assert.match(html, /6 MP；生命上限 37/);
});

test('mechanic sentences bind only explicitly named effect fields', () => {
  const html = render([
    '敌方单体｜射程 {{spell.effects.0.射程}} 格',
    '以 {{spell.effects.0.成功率}} 的技能值检定，造成 {{spell.effects.0.伤害骰}} 伤害。',
    '获得 {{spell.effects.0.成功率奖惩骰}} 颗攻击奖励骰，并使{{spell.effects.0.技能加减值}}。',
    '伤害降低 10%；并按伤害的 100% 回复自身生命。',
    '目标受到攻击降低 5，持续 {{spell.effects.0.持续回合}} 轮。',
  ], { character: { attrs: {} }, spell: { effects: [
    { 射程: 7, 成功率: 75, 伤害骰: '2d6+4', 持续回合: 6, 成功率奖惩骰: 2, 技能加减值: '行动力+9', 受到伤害百分比: 0.25, 吸血比例: 0.75 },
  ] } });
  assert.match(html, /射程 7 格/);
  assert.match(html, /75 的技能值检定，造成 2d6\+4 伤害/);
  assert.match(html, /获得 2 颗攻击奖励骰，并使行动力\+9/);
  assert.match(html, /伤害降低 10%；并按伤害的 100% 回复自身生命/);
  assert.match(html, /持续 6 轮/);
});

test('character profile stats and basic attack dice bind to current character data', () => {
  const html = render('生命：{{character.attrs.体力上限}}；敏捷：{{character.attrs.敏捷}}。普通攻击基础骰：{{character.str_attrs.伤害值}}。', {
    character: { attrs: { 体力上限: 41, 敏捷: 83 }, str_attrs: { 伤害值: '2d8+3' } },
    spell: {},
  });
  assert.match(html, /<strong class="prose-label">生命：<\/strong>41；敏捷：83/);
  assert.match(html, /普通攻击基础骰：2d8\+3/);
});

test('domain and status values bind to their named fields', () => {
  const html = render([
    '每轮为区域内敌人增加 {{spell.effects.0.冰冻每轮层数}} 层冰冻。',
    '在自身周围 {{spell.effects.0.作用半径}} 格创建领域，同时限 {{spell.effects.0.领域同时上限}} 处。',
  ], { character: { attrs: {} }, spell: { effects: [{ 冰冻每轮层数: 4, 作用半径: 9, 领域同时上限: 2 }] } });
  assert.match(html, /增加 4 层冰冻/);
  assert.match(html, /周围 9 格创建领域，同时限 2 处/);
});

test('colon attributes and bold Markdown attributes render as tables across blank lines', () => {
  for (const content of [
    ['生命：24', '基础行动力：6', '敏捷：70', '斗殴：55', '攻击伤害：1d6+1d4', '闪避：35', '飞行：是', '防御反应：闪避、反击'],
    '**生命**24\n\n**基础行动力**6\n\n**敏捷**70\n\n**斗殴**55\n\n**攻击伤害**1d6+1d4\n\n**闪避**35\n\n**飞行**是\n\n**防御反应**闪避、反击',
  ]) {
    const html = render(content);
    assert.equal((html.match(/<table /g) || []).length, 1);
    assert.equal((html.match(/scope="row"/g) || []).length, 8);
    assert.match(html, /<th scope="row">攻击伤害<\/th><td>1d6\+1d4<\/td>/);
    assert.match(html, /<th scope="row">防御反应<\/th><td>闪避、反击<\/td>/);
  }
});

test('headings and prose separate parameter tables and layer rules remain lists', () => {
  const html = render(['生命：24', '小雪人', '敏捷：50', '说明：命中后触发。每轮分别计算。', '第 1 层：伤害提高 18%。']);
  assert.equal((html.match(/<table /g) || []).length, 2);
  assert.match(html, /<\/table><h5>小雪人<\/h5><table/);
  assert.match(html, /<p><strong class="prose-label">说明：<\/strong>命中后触发。每轮分别计算。<\/p>/);
  assert.match(html, /<ul class="prose-levels">/);
});

test('lightweight Markdown handles emphasis, code, lists and quotes while escaping HTML', () => {
  const html = render('## 规则\n\n- **命中**后触发\n- 使用 `1d6`\n3. 第三项\n> <script>alert(1)</script>');
  assert.match(html, /<h5>规则<\/h5><ul><li><strong>命中<\/strong>后触发<\/li>/);
  assert.match(html, /<code>1d6<\/code>/);
  assert.match(html, /<ol start="3"><li>第三项<\/li><\/ol>/);
  assert.match(html, /<blockquote>&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});

test('approved Y9–Y12 prose renders every paragraph and summon section without losing text', () => {
  const approved = require('./fixtures/y9-y12-approved.json');
  for (const profile of Object.values(approved)) {
    for (const skill of profile.skills) {
      const html = render([skill.description, ...skill.facts]);
      // Parameter labels and values occupy separate cells; reconstruct their
      // punctuation before comparing them with the approved parameter lines.
      const text = html.replace(/<\/th><td>/g, '：</th><td>').replace(/<[^>]+>/g, '');
      for (const line of [skill.description, ...skill.facts]) {
        assert.ok(text.includes(line.replace(/^#{1,6}\s+/, '')), `${skill.name}: ${line}`);
      }
    }
  }
  const summon = approved.Y12.skills.find(s => s.name === '空间引入');
  const html = render(summon.facts);
  // The approved merge-stat paragraph adds a fourth parameter table.
  assert.equal((html.match(/<table /g) || []).length, 4);
  for (const heading of ['召唤与保护', '玄武', '朱雀', '龙虎', '三合一']) {
    assert.ok(html.includes(`<h5>${heading}</h5>`));
  }
});

test('stage-three approved passives and skill paragraphs render without losing text', () => {
  const approved = require('./fixtures/stage3-approved.json');
  for (const [serial, profile] of Object.entries(approved)) {
    const groups = [profile.passives || [], ...(profile.skills || []).map(skill =>
      [skill.description, ...(skill.facts || [])].filter(Boolean))];
    for (const lines of groups) {
      const text = render(lines).replace(/<\/th><td>/g, '：</th><td>').replace(/<[^>]+>/g, '');
      for (const line of lines) {
        assert.ok(text.includes(line.replace(/^#{1,6}\s+/, '')), `${serial}: ${line}`);
      }
    }
  }
});
