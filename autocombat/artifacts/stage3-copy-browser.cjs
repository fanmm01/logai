const { chromium } = require('../frontend/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const proposal = require('../frontend/tests/fixtures/stage3-approved.json');
const profiles = require('../frontend/src/data/encyclopedia.json');
const output = path.join(__dirname, 'stage3-approved-browser');
(async () => {
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({channel:'msedge',headless:true});
  const page = await browser.newPage();
  const errors=[], cases=[];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/api/ui/catalog', route => route.fulfill({json:{characters:
    Object.entries(profiles).map(([serial,p]) => ({serial,name:serial,attrs:{},spells:p.skills.map(s=>({name:s.name,phase:s.phase}))}))}}));
  try {
    for (const width of [1440,390]) {
      await page.setViewportSize({width,height:width===390?844:1000});
      for (const [serial, patch] of Object.entries(proposal)) {
        await page.goto('http://127.0.0.1:8891/ui/library?character='+serial);
        const modal=page.locator('.encyclopedia-modal');
        await modal.locator('.skill-meta').first().waitFor();
        await modal.locator('[data-skill-index]').evaluateAll(nodes=>nodes.forEach(el=>el.open=true));
        const text=await modal.evaluate(el=>{
          const copy=el.cloneNode(true);
          // Parameter prose uses a table; restore its source colon for comparison.
          copy.querySelectorAll('th').forEach(th=>th.append('：'));
          return copy.textContent;
        });
        for (const article of patch.skills||[]) {
          if(article.description) assert.ok(text.includes(article.description),serial+': '+article.name);
          for(const fact of article.facts||[]) {
            if(!fact.startsWith('###') && fact.length>50) assert.ok(text.includes(fact),serial+': '+fact);
          }
        }
        if(patch.passives) for(const fact of patch.passives) assert.ok(text.includes(fact),serial+': passive');
        const size=await modal.evaluate(el=>({client:el.clientWidth,scroll:el.scrollWidth}));
        assert.ok(size.scroll<=size.client+1,JSON.stringify({serial,width,...size}));
        const selected=(patch.skills||[]).at(-1);
        if(selected) {
          const index=profiles[serial].skills.findIndex(a=>a.name===selected.name&&a.phase===selected.phase);
          await modal.locator(`[data-skill-index="${index}"]`).scrollIntoViewIfNeeded();
        }
        await page.screenshot({path:path.join(output,`${serial}-copy-${width}.png`)});
        cases.push({serial,width,...size});
      }
    }
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'copy-report.json'),JSON.stringify({status:'approved production build',cases,errors},null,2));
    console.log(JSON.stringify({cases:cases.length,errors}));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
