/* Real browser integration against an isolated UI server (UI_BASE_URL). */
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const {chromium}=require(path.resolve(__dirname,'../../../frontend/node_modules/playwright'));
const base=process.env.UI_BASE_URL||'http://127.0.0.1:8895';
const artifacts=process.env.UI_TEST_ARTIFACTS||path.resolve(__dirname,'../../../.tmpwork/story-browser-artifacts');
(async()=>{
  fs.mkdirSync(artifacts,{recursive:true});const browser=await chromium.launch({channel:process.env.UI_BROWSER_CHANNEL||'msedge',headless:true,args:['--autoplay-policy=no-user-gesture-required']});
  try{
    const page=await browser.newPage({viewport:{width:1500,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/ui/story');await page.locator('[data-project="astral-demo"] .story-chapter').first().waitFor();assert.equal(await page.locator('[data-project="astral-demo"] .story-chapter').count(),3);
    assert.equal(await page.locator('[data-project="astral-demo"] .story-chapter.locked').count(),1);
    await page.screenshot({path:path.join(artifacts,'chapters.png')});await page.locator('[data-project="astral-demo"] .story-chapter').first().getByRole('button').click();
    const getRun=()=>page.evaluate(async()=>{const id=localStorage.getItem('story-run');return fetch('/api/ui/story/runs/'+id).then(r=>r.json());});
    let sawFive=false,sawChoice=false;
    for(let i=0;i<80;i++){
      const run=await getRun();if(run.status==='battle_ready')break;
      if(run.status==='choice'){sawChoice=true;assert.ok(run.choice.options.every(o=>o.enabled));await page.locator('.story-choice').first().click();}
      else if(run.status==='text'){
        if((run.player_snapshot?.nowStageState?.freeFigure||[]).length===5){sawFive=true;await page.screenshot({path:path.join(artifacts,'five-figures.png')});}
        const frame=page.frames().find(f=>f.url().includes('/story-engine/'));
        if(frame)await frame.locator('body').click({position:{x:400,y:220},force:true});
      }
      await page.waitForTimeout(250);
    }
    assert.equal((await getRun()).status,'battle_ready');assert.ok(sawChoice);assert.ok(sawFive,'five-figure command was not observed');
    for(const viewport of [{width:430,height:900},{width:844,height:390}]){
      await page.setViewportSize(viewport);const dialog=await page.getByRole('dialog',{name:'确认阵容'}).boundingBox();
      assert.ok(dialog.x>=0&&dialog.y>=50&&dialog.x+dialog.width<=viewport.width&&dialog.y+dialog.height<=viewport.height,JSON.stringify(dialog));
      assert.ok(await page.getByRole('button',{name:'开始战斗',exact:true}).isVisible());
      await page.screenshot({path:path.join(artifacts,'encounter-'+viewport.width+'.png')});
    }
    await page.setViewportSize({width:1500,height:1000});
    await page.screenshot({path:path.join(artifacts,'encounter.png'),fullPage:true});
    // Pause at creation so a fast AI victory cannot race the mid-battle save test.
    await page.route('**/api/ui/story/runs/*/commands',async route=>{
      const response=await route.fetch();const result=await response.json();
      if(route.request().postDataJSON()?.op==='startBattle'&&result.room)await page.request.post(base+'/api/ui/rooms/'+result.room+'/control',{data:{op:'pause'}});
      await route.fulfill({response});
    });
    await page.getByRole('button',{name:'开始战斗',exact:true}).click();await page.locator('.battle-page').waitFor({timeout:30000});
    assert.ok((await getRun()).room);await page.screenshot({path:path.join(artifacts,'battle.png')});
    await page.locator('.story-battle-banner').getByRole('button',{name:'保存战斗',exact:true}).click();
    await page.waitForTimeout(400);await page.locator('.story-battle-banner').getByRole('button',{name:'剧情与存档',exact:true}).click();
    await page.getByRole('button',{name:'菜单',exact:true}).click();await page.getByRole('button',{name:'读档',exact:true}).click();await page.waitForTimeout(800);assert.equal((await getRun()).status,'battle');
    // The restored room must be paused and retain the complete encounter identity.
    const run=await getRun();const room=await page.evaluate(code=>fetch('/api/ui/rooms/'+code).then(r=>r.json()),run.room);assert.equal(room.running,false);assert.equal(room.story.game,'training.gmf');
    await page.setViewportSize({width:430,height:900});await page.getByRole('button',{name:'章节列表',exact:true}).click();await page.locator('[data-project="astral-demo"] .story-chapter').first().waitFor();await page.screenshot({path:path.join(artifacts,'chapters-mobile.png'),fullPage:true});
    assert.equal(errors.length,0,errors.join('\n'));console.log('Story browser: chapters, five figures, choice unlock, encounter, battle and save/load passed');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
