const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const {loadTs} = require('./load-ts.cjs');
const {Fields} = loadTs(path.resolve(__dirname, '../src/Expansion.tsx'));

const render = value => renderToStaticMarkup(React.createElement(Fields, {
  value, name: 'spells', change: () => {}, enums: {}, samples: {},
}));

test('skill identity is visible and read only, with stable numbering after reordering', () => {
  const html = render([
    {name: 'Later', skill_id: 'Y17-S003', skill_index: 3},
    {name: 'Earlier', skill_id: 'Y17-S002', skill_index: 2, _disabled: true},
  ]);
  assert.match(html, /技能 3 · Later/);
  assert.match(html, /技能 2 · Earlier · 已禁用/);
  assert.match(html, /<output aria-label="技能唯一编号">Y17-S003<\/output>/);
  assert.match(html, /<output aria-label="固定技能编号">3<\/output>/);
  assert.doesNotMatch(html, /删除属性 skill_(?:id|index)/);
  assert.doesNotMatch(html, /<input[^>]+aria-label="(?:技能唯一编号|固定技能编号)"/);
});

test('new skills wait for backend identity allocation', () => {
  assert.match(render([{name: 'New'}]), /技能 待分配 · New/);
});
