const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { loadTs } = require('./load-ts.cjs');
const { withEncyclopedia } = loadTs(path.resolve(__dirname, '../src/encyclopedia.ts'));
const source = JSON.parse(execFileSync('python', ['-X', 'utf8', '-c',
  'import json, characters_data as c; print(json.dumps(dict(cards=c.ALL_CHARACTERS,summons=c.SUMMON_TEMPLATES,items=c.ITEM_TEMPLATES),ensure_ascii=False))',
], { cwd: path.resolve(__dirname, '../..'), encoding: 'utf8' }));
const rawCatalog = source.cards.map(card => ({ ...card, summon_templates: source.summons, item_templates: source.items }));
const catalog = rawCatalog.map(withEncyclopedia);
module.exports = { ...source, rawCatalog, catalog, profiles: Object.fromEntries(catalog.map(c => [c.serial, c])) };
