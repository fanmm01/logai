const fs = require('node:fs');
const path = require('node:path');
const { loadTs } = require('./load-ts.cjs');
const { withEncyclopedia } = loadTs(path.resolve(__dirname, '../../frontend/src/encyclopedia.ts'));
const { cards, summons, items } = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(Object.fromEntries(cards.map(card => [card.serial,
  withEncyclopedia({ ...card, summon_templates: summons, item_templates: items }),
]))));
