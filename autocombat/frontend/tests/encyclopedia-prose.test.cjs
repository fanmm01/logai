const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { loadTs } = require('./load-ts.cjs');
const loaded = { exports: loadTs(path.resolve(__dirname, '../src/EncyclopediaProse.tsx')) };
const render = content => renderToStaticMarkup(React.createElement(loaded.exports.EncyclopediaProse, { content })).replace(/ data-definition-source="[^"]*"/g, '');

test('existing snowman rules become headings and a contiguous eight-level list without losing text', () => {
  const { Y6 } = require('../src/data/encyclopedia.json');
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
