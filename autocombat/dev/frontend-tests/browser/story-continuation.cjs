const assert=require('node:assert/strict'),path=require('node:path');
const {chromium}=require(path.resolve(__dirname,'../../../frontend/node_modules/playwright'));
const base=process.env.UI_BASE_URL||'http://127.0.0.1:8895';
(async()=>{
  const browser=await chromium.launch({channel:process.env.UI_BROWSER_CHANNEL||'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1450,height:1000}});page.on('dialog',dialog=>dialog.dismiss());await page.goto(base+'/ui/story');await page.locator('[data-project="astral-demo"] .story-chapter').nth(1).getByRole('button').click();
    const api=(url,data)=>page.evaluate(async({url,data})=>{const r=await fetch(url,data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{});return {status:r.status,data:await r.json()};},{url,data});
    const getRun=async()=>{const id=await page.evaluate(()=>localStorage.getItem('story-run'));return (await api('/api/ui/story/runs/'+id)).data;};
    for(let i=0;i<60;i++){
      const run=await getRun();if(run.status==='battle_ready')break;
      if(run.status==='choice')await api('/api/ui/story/runs/'+run.id+'/commands',{commandId:'choice-'+i,expectedRevision:run.revision,op:'choose',payload:{id:run.choice.options.find(o=>o.enabled).id}});
      else if(run.status==='text')await api('/api/ui/story/runs/'+run.id+'/commands',{commandId:'text-'+i,expectedRevision:run.revision,op:'advance',payload:{token:run.scene.token}});
      await page.waitForTimeout(70);
    }
    await page.getByRole('button',{name:'开始战斗',exact:true}).click();await page.locator('.battle-page').waitFor({timeout:30000});let run=await getRun();let room=(await api('/api/ui/rooms/'+run.room)).data;const waves=new Set([0]);
    await api('/api/ui/rooms/'+room.code+'/control',{op:'pause'});
    for(let i=0;i<650;i++){
      run=await getRun();if(run.status!=='battle')break;room=(await api('/api/ui/rooms/'+run.room)).data;waves.add(room.story.wave);
      if(room.snapshot.state.phase==='ended'&&!room.story.finished){await api('/api/ui/rooms/'+room.code+'/control',{op:'play'});await page.waitForTimeout(500);}
      else await api('/api/ui/rooms/'+room.code+'/control',{op:'step'});
    }
    assert.ok(waves.has(1));run=await getRun();assert.notEqual(run.status,'battle');
    for(let i=0;i<10&&run.status==='text';i++){await api('/api/ui/story/runs/'+run.id+'/commands',{commandId:'finish-'+i,expectedRevision:run.revision,op:'advance',payload:{token:run.scene.token}});run=await getRun();}
    assert.equal(run.status,'continue');await page.locator('.story-battle-banner').getByRole('button',{name:'返回剧情',exact:true}).click();
    const next=page.locator('.story-state-card').getByRole('button').filter({hasText:'黎明的回声'});await next.waitFor();await next.click();
    await page.waitForTimeout(600);const third=await getRun();assert.equal(third.flow,'MC2.flow');assert.equal(third.variables.signal,2);assert.notEqual(third.entry,'START');
    console.log('Story continuation browser: real two-wave AI battle, continuation prompt and chapter-three state transfer passed');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
