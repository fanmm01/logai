// Run against a local built UI. Writes are intercepted; no character or tournament data is changed.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8890';
const profiles = require('../../src/data/encyclopedia.json');
const artifacts = process.env.UI_TEST_ARTIFACTS;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

(async()=>{
  const browser=await chromium.launch({channel:process.env.UI_BROWSER_CHANNEL || 'msedge',headless:true});
  try {
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    const page=await context.newPage(),errors=[],checks=[];
    page.on('pageerror',error=>errors.push(String(error)));
    // Keep character fixtures deterministic even when the user's enabled roster changes.
    const live=await (await context.request.get(base+'/api/ui/catalog')).json();
    const names=new Map(live.characters.map(c=>[c.serial,c.name]));
    const { rawCatalog, summons, items } = require('../catalog-fixture.cjs');
    const characters=rawCatalog.map(card=>({...card,name:names.get(card.serial)||card.name,portrait:'',avatar:''}));
    const tournament={id:'ui-preview-fixture',format:'1v1',phase:'regular',status:'paused',revision:1,teams:[['Y1','Y2'],['Y3']],
      standings:[{id:0,points:1,wins:1,battles:1},{id:1,points:0,wins:0,battles:1}],schedule:[],completed:0,series:[],results:[]};
    await page.route('**/api/ui/catalog',route=>route.fulfill({json:{characters,summon_templates:summons,item_templates:items}}));
    await page.route('**/api/ui/tournaments',route=>route.request().method()==='GET'?route.fulfill({json:{tournaments:[tournament]}}):route.abort());
    await page.route('**/api/ui/session',route=>route.fulfill({json:{id:'ui-preview-test'}}));
    await page.goto(base+'/ui/home');
    await page.locator('.home-roster .character-card').first().waitFor();
    await page.getByRole('button',{name:'进入锦标赛',exact:true}).click();
    await page.waitForURL('**/ui/tournament');
    const rankLinks=page.locator('.tournament-layout table .knowledge-link');
    await rankLinks.first().waitFor();
    assert.equal(await rankLinks.count(),3);
    await rankLinks.first().hover();await pause(1100);await page.locator('.character-preview').waitFor();
    await page.locator('.character-preview .preview-body').click();
    await page.locator('.encyclopedia-modal').waitFor();await page.keyboard.press('Escape');
    await page.locator('.encyclopedia-modal').waitFor({state:'hidden'});
    checks.push('launcher and individual tournament character links');

    await page.getByRole('button',{name:'角色图鉴',exact:true}).click();
    const card=page.locator('.library-grid .character-card').first();
    await card.hover();await pause(500);assert.equal(await page.locator('.character-preview').count(),0);
    await page.mouse.move(1400,80);await pause(650);assert.equal(await page.locator('.character-preview').count(),0);
    await card.hover();await pause(1100);await page.locator('.character-preview').waitFor();
    await page.locator('.character-preview').hover();await pause(250);
    assert.equal(await page.locator('.character-preview .knowledge-link').count(),0);
    const bounds=await page.locator('.character-preview').boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=1440);
    await page.locator('.character-preview .preview-body').click();
    const modal=page.locator('.encyclopedia-modal');await modal.waitFor();
    const term=modal.locator('.knowledge-link').filter({hasText:'MP'}).first();
    await term.hover();await pause(1100);await page.locator('.term-preview').waitFor();
    await page.keyboard.press('Escape');assert.ok(await modal.isVisible());
    await term.click();await modal.locator('.definition-panel').waitFor();
    assert.match(await modal.locator('.definition-panel').innerText(),/召唤物使用自身 MP/);
    await modal.locator('.profile-stats .knowledge-link').filter({hasText:/^闪避$/}).click();
    assert.match(await modal.locator('.definition-panel').innerText(),/攻击极难 3、闪避困难 2/);
    assert.match(await modal.locator('.definition-panel').innerText(),/原攻击为普通失败 -1 时加 2/);
    await modal.locator('.definition-related').first().click();
    await modal.getByRole('button',{name:'← 返回',exact:true}).click();
    if(artifacts){fs.mkdirSync(artifacts,{recursive:true});await page.screenshot({path:path.join(artifacts,'definition-desktop.png')});}
    await page.keyboard.press('Escape');assert.equal(await modal.locator('.definition-panel').count(),0);
    await page.keyboard.press('Escape');await modal.waitFor({state:'hidden'});
    assert.ok(!page.url().includes('character='));
    await page.goForward();await modal.waitFor();
    await page.reload();await modal.waitFor();await page.keyboard.press('Escape');await modal.waitFor({state:'hidden'});
    checks.push('delayed previews, pointer transfer, no nested glossary, definition panel and history');

    // The footer follows the displayed definition/section and preserves back navigation.
    await page.goto(base+'/ui/library?character=Y2&term='+encodeURIComponent('rule-魔力'));
    await modal.locator('.definition-help').click();
    await page.waitForURL('**/ui/help/mechanics#rule-MP');
    await modal.waitFor({state:'hidden'});
    assert.ok(await page.locator('[id="rule-MP"]').isVisible());
    assert.ok((await page.locator('[id="rule-MP"]').boundingBox()).y<150);
    await page.goBack();await modal.locator('.definition-help').waitFor();
    assert.match(await modal.locator('.definition-panel').innerText(),/魔力/);
    await modal.locator('.definition-related').first().click();
    await modal.locator('.definition-help').click();
    await page.waitForURL('**/ui/help/mechanics#mechanics-2');
    assert.ok((await page.locator('#mechanics-2').boundingBox()).y<150);
    for(const [serial,id] of [['Y6','Y6-term-冰冻'],['Y2','Y2-skill-火球术']]){
      await page.goto(base+'/ui/library?character='+serial+'&term='+encodeURIComponent(id));
      await modal.locator('.definition-help').click();
      await page.waitForURL(url=>url.pathname==='/ui/help/mechanics'&&decodeURIComponent(url.hash.slice(1))===id);
      const entry=page.locator('[id="'+id+'"]');await entry.waitFor({state:'visible'});
      assert.ok(await entry.evaluate(el=>el.closest('details').open));
      assert.ok((await entry.boundingBox()).y<150);
      await page.reload();await entry.waitFor({state:'visible'});
      assert.ok((await entry.boundingBox()).y<150);
    }
    checks.push('definition footer targets canonical terms, selected sections and expanded local entries; back and reload restore context');

    await page.getByRole('button',{name:'自由对战',exact:true}).click();
    const selectable=page.locator('.selection-grid .character-card').first();
    const selected=await selectable.getAttribute('class');await selectable.click();
    assert.notEqual(await selectable.getAttribute('class'),selected);
    await page.locator('.selection-grid .card-info').first().click();await modal.waitFor();
    await page.keyboard.press('Escape');await modal.waitFor({state:'hidden'});
    checks.push('team selection remains intact and independent info opens details');

    await page.getByRole('button',{name:'帮助',exact:true}).click();
    await page.waitForURL('**/ui/help/mechanics');assert.equal(await page.locator('.mechanics-article>section').count(),8);
    const search=page.getByRole('searchbox',{name:'搜索术语或机制'});
    await search.fill('闪避');
    await page.locator('[id="rule-闪避"]').waitFor();
    assert.match(await page.locator('[id="rule-闪避"]').innerText(),/攻击极难 3、闪避困难 2/);
    await page.locator('nav[aria-label="机制目录"]').getByRole('link',{name:'防御与多段攻击',exact:true}).click();
    assert.ok(page.url().endsWith('#mechanics-4'));
    await search.fill('y17');
    assert.equal(await page.locator('.mechanics-article>section').count(),0);
    assert.equal(await page.locator('.mechanics-character').count(),1);
    assert.equal(await page.locator('.mechanics-character[open]').count(),1);
    assert.match(await page.locator('.mechanics-character').innerText(),/召唤物不提供血液/);
    await search.fill('no-matching-mechanics-term');await page.getByText('没有找到相关条目，请尝试其他术语、角色名或编号。').waitFor();
    await page.getByRole('button',{name:'清除搜索',exact:true}).click();
    assert.equal(await page.locator('.mechanics-article>section').count(),8);
    assert.equal(await page.locator('.mechanics-character[open]').count(),0);
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:width===390?844:1000});
      const size=await page.locator('.mechanics-article').evaluate(el=>({client:el.clientWidth,scroll:el.scrollWidth}));
      assert.ok(size.scroll<=size.client+1,`mechanics at ${width}: ${JSON.stringify(size)}`);
      if(artifacts)await page.screenshot({path:path.join(artifacts,`mechanics-${width}.png`)});
    }
    await page.setViewportSize({width:1440,height:1000});
    checks.push('complete glossary entries, category anchors, case-insensitive specialist search, empty state and responsive help');
    await page.getByRole('button',{name:'角色卡帮助',exact:true}).click();assert.equal(await page.locator('.help-character-list a').count(),characters.length);
    assert.equal(await page.locator('.help-character-list .character-card').count(),0);
    assert.equal(await page.getByTitle('界面设置',{exact:true}).count(),0);
    await page.getByRole('button',{name:'设置',exact:true}).click();
    await page.locator('.settings-content input').first().fill('preview-unsaved-name');
    page.once('dialog',dialog=>dialog.dismiss());await page.goBack();await pause(200);
    assert.ok(page.url().includes('/ui/settings'));
    assert.equal(await page.locator('.settings-content input').first().inputValue(),'preview-unsaved-name');
    page.once('dialog',dialog=>dialog.accept());await page.goBack();await page.waitForURL('**/ui/help/characters');
    checks.push('help routes, simplified settings and cancel/accept browser back with unsaved settings');

    // Editor saves use an isolated in-memory API response and never reach the real save endpoints.
    const fixture={version:'preview-version',characters:characters.map(c=>({...c,inventory:[],str_attrs:{}})),
      enabled_serials:characters.map(c=>c.serial),reserved:[],summons:{},items:{},fields:{},enums:{},settings:{}};
    let data=structuredClone(fixture),saves=0,fail=false,allowlist=['127.0.0.1'],accessSaves=0;
    await page.route('**/api/ui/data',route=>route.fulfill({json:data}));
    await page.route('**/api/ui/backups',route=>route.fulfill({json:{backups:[]}}));
    await page.route('**/api/ui/training',route=>route.fulfill({json:{jobs:[]}}));
    await page.route('**/api/ui/data/save',async route=>{
      saves++;await pause(200);
      if(fail)return route.fulfill({status:400,json:{message:'preview-save-failed'}});
      data=route.request().postDataJSON();return route.fulfill({json:{backup:'preview',version:data.version}});
    });
    await page.route('**/api/ui/allowlist',route=>{
      if(route.request().method()==='POST'){accessSaves++;allowlist=route.request().postDataJSON().allowlist.split('\n');}
      return route.fulfill({json:{allowlist}});
    });
    await page.getByRole('button',{name:'角色管理',exact:true}).click();
    const input=page.locator('.editor-content input[aria-label="名称"]').first();await input.waitFor();
    await page.keyboard.press('Control+s');await pause(100);assert.equal(saves,0);
    await input.fill('preview-renamed');
    await page.locator('.management-page h1').click();await page.keyboard.press('Control+z');
    assert.equal(await input.inputValue(),fixture.characters[0].name);
    await page.keyboard.press('Control+y');assert.equal(await input.inputValue(),'preview-renamed');
    await page.getByRole('button',{name:'撤销'}).click();assert.equal(await input.inputValue(),fixture.characters[0].name);
    await page.keyboard.press('Control+Shift+z');assert.equal(await input.inputValue(),'preview-renamed');
    await page.keyboard.press('Control+s');await page.keyboard.press('Control+s');
    await page.getByText('已保存，并自动备份：preview',{exact:true}).waitFor();assert.equal(saves,1);
    assert.equal(await input.inputValue(),'preview-renamed');
    fail=true;await input.fill('preview-failure-draft');await page.keyboard.press('Control+s');
    await page.getByText('preview-save-failed',{exact:true}).waitFor();assert.equal(await input.inputValue(),'preview-failure-draft');
    await page.getByRole('button',{name:'访问白名单',exact:true}).click();
    await page.locator('.allowlist-editor textarea').fill('127.0.0.1\n192.168.1.0/24');await page.keyboard.press('Control+s');
    await page.getByText('访问白名单已保存',{exact:true}).waitFor();assert.equal(accessSaves,1);assert.equal(saves,2);
    await page.keyboard.press('Control+s');await pause(150);assert.equal(accessSaves,1);
    checks.push('undo/redo buttons and Ctrl/Command shortcuts; Ctrl+S save and allowlist');

    // Local definitions are plain only when nearby and visible with their reference.
    await page.setViewportSize({width:1440,height:1800});
    await page.goto(base+'/ui/library?character=Y2');await modal.locator('.skill-meta').first().waitFor();
    const fireball=modal.locator('.trait-definitions [data-local-term="Y2-skill-火球术"]');
    await page.waitForFunction(()=>{
      const reference=document.querySelector('.trait-definitions [data-local-term="Y2-skill-火球术"]');
      return reference&&!reference.querySelector('a');
    });
    await page.setViewportSize({width:1440,height:500});
    await fireball.locator('a').waitFor();
    await page.setViewportSize({width:1440,height:1800});
    await fireball.locator('a').waitFor({state:'detached'});
    await modal.locator('[data-skill-index="0"]').evaluate(el=>el.open=false);
    await fireball.locator('a').waitFor();
    assert.equal(await modal.locator('[data-skill-index="0"] summary [data-local-term="Y2-skill-火球术"] a').count(),0,'a collapsed skill title must never link to itself');
    await page.setViewportSize({width:1440,height:500});
    await fireball.locator('a').waitFor();
    assert.equal(await modal.locator('[data-skill-index="0"] summary [data-local-term="Y2-skill-火球术"] a').count(),0,'off-screen definitions must not create self links');
    await page.setViewportSize({width:1440,height:1800});
    await modal.locator('[data-skill-index="0"]').evaluate(el=>el.open=true);
    await fireball.locator('a').waitFor({state:'detached'});
    await page.setViewportSize({width:1440,height:2600});
    await page.goto(base+'/ui/library?character=Y16');await modal.locator('.skill-meta').first().waitFor();
    await modal.locator('[data-skill-index]').evaluateAll(nodes=>nodes.forEach(el=>el.open=[7,9].includes(Number(el.dataset.skillIndex))));
    const distant=modal.locator('[data-skill-index="7"] .encyclopedia-prose [data-local-term="Y16-skill-万众瞩目的乌托邦"]');
    await distant.locator('a').waitFor();
    const sameScreen=await modal.evaluate(root=>{
      const bounds=root.getBoundingClientRect();
      const definition=root.querySelector('[data-skill-index="9"] .encyclopedia-prose').getBoundingClientRect();
      const reference=root.querySelector('[data-skill-index="7"] .encyclopedia-prose [data-local-term="Y16-skill-万众瞩目的乌托邦"]').getBoundingClientRect();
      return definition.top>=bounds.top&&definition.bottom<=bounds.bottom&&reference.top>=bounds.top&&reference.bottom<=bounds.bottom;
    });
    assert.ok(sameScreen,'intervening-skill rule applies even with both definition and reference on screen');
    await page.setViewportSize({width:1440,height:500});
    await page.goto(base+'/ui/library?character=Y6');await modal.locator('.skill-meta').first().waitFor();
    const ownHeading=modal.locator('h5 [data-local-term="Y6-term-八层循环"]');
    await ownHeading.waitFor({state:'attached'});await pause(150);
    assert.equal(await ownHeading.locator('a').count(),0,'a definition section heading must never link to its own off-screen body');
    checks.push('local term links update on resize, collapse and distance; self references never link');

    const layout=[];
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:width===390?844:1000});
      for(const serial of Object.keys(profiles)){
        await page.goto(base+'/ui/library?character='+serial);await modal.locator('.skill-meta').first().waitFor();
        assert.doesNotMatch(await modal.innerText(), /\{\{|未配置/, `${serial}: every reference resolves in the browser`);
        await modal.locator('.knowledge-link').filter({hasText:'MP'}).first().click();
        await modal.locator('.definition-panel').waitFor();
        if(width===390) {
          await pause(100);
          const bounds=await modal.locator('.definition-panel').boundingBox();
          assert.ok(bounds.y>=0&&bounds.y<700,`${serial}: definition must appear in the mobile viewport`);
        }
        const sizes=await modal.evaluate(el=>({client:el.clientWidth,scroll:el.scrollWidth,panel:el.querySelector('.definition-panel').getBoundingClientRect().width}));
        assert.ok(sizes.scroll<=sizes.client+1,`${serial} at ${width}: ${JSON.stringify(sizes)}`);
        assert.ok(sizes.panel>150);
        layout.push({serial,width,...sizes});
        if(artifacts&&['Y6','Y14','Y16'].includes(serial))await page.screenshot({path:path.join(artifacts,`${serial}-${width}.png`)});
      }
    }
    // A saved room may restore in the background without overriding an explicit page.
    const room={code:'PREV01',name:'preview',host:'ui-preview-test',members:[],revision:1,history:[],local:true,
      running:false,speed:1,controls:{},invite_urls:[],snapshot:{state:{phase:'preparation',round:0,activeIndex:0},
        map:{width:8,height:8,obstacles:{},occupants:{}},units:[],initiative:[],turn:null,effects:[],graveyard:[],reaction:null,result:null,revision:1}};
    await page.route('**/api/ui/rooms/PREV01',route=>route.fulfill({json:room}));
    await page.route('**/api/ui/rooms/PREV01/events*',route=>route.fulfill({contentType:'text/event-stream',body:': preview\n\n'}));
    await page.goto(base+'/ui/library?room=PREV01');await page.locator('.library-grid').waitFor();await pause(250);
    assert.ok(page.url().includes('/ui/library'));
    await page.goto(base+'/ui/?room=PREV01');await page.waitForURL('**/ui/battle?room=PREV01');
    checks.push('background room restoration and legacy invitation routing');
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({checks,layoutCases:layout.length,errors},null,2));
    if(artifacts)fs.writeFileSync(path.join(artifacts,'report.json'),JSON.stringify({checks,layout,errors},null,2));
    await context.close();
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
