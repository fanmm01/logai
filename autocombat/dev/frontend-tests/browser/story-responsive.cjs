/* Real mobile/landscape/fullscreen and graph navigation regression checks. */
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),os=require('node:os');
const {chromium}=require(path.resolve(__dirname,'../../../frontend/node_modules/playwright'));
const base=process.env.UI_BASE_URL||'http://127.0.0.1:8895';
const artifacts=process.env.UI_TEST_ARTIFACTS||path.resolve(__dirname,'../../../.tmpwork/story-responsive-artifacts');
(async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'story-responsive-'));
  const id='responsive-'+Date.now();
  fs.mkdirSync(path.join(root,'background'));
  fs.copyFileSync(path.resolve(__dirname,'../../../story-projects/demo/background/courtyard.svg'),path.join(root,'background/courtyard.svg'));
  fs.mkdirSync(path.join(root,'figure'));
  fs.writeFileSync(path.join(root,'figure/guide.svg'),'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="900"><rect width="300" height="900" fill="#8876ac"/><circle cx="150" cy="110" r="80" fill="#ffe1bf"/></svg>');
  fs.writeFileSync(path.join(root,'story.project.json'),JSON.stringify({id,name:'Responsive fixture',figures:{Guide:'guide.svg'}}));
  fs.writeFileSync(path.join(root,'A.flow'),'@START:\n->dialog\n->END\n');
  fs.writeFileSync(path.join(root,'dialog.txt'),'\\bg courtyard.svg\nNarrator:“First responsive line.”\n“Second responsive line.”\n“Third responsive line.”\n“Fourth responsive line.”\n\\c{\n1“Continue”;\n2“Stay”;\n}\n\\c1 “The first path.”\n\\c2 “The second path.”\n\\c\n');
  fs.mkdirSync(artifacts,{recursive:true});
  fs.writeFileSync(path.join(root,'dialog.txt'),'\\mem Guide\n'+fs.readFileSync(path.join(root,'dialog.txt'),'utf8'));
  const browser=await chromium.launch({channel:process.env.UI_BROWSER_CHANNEL||'msedge',headless:true,args:['--autoplay-policy=no-user-gesture-required']});
  try{
    const page=await browser.newPage({viewport:{width:430,height:900},isMobile:true,hasTouch:true});const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base+'/ui/story');await page.getByRole('button',{name:'创作工作区'}).click();
    await page.getByRole('textbox',{name:'工作区目录'}).fill(root);await page.getByRole('button',{name:'打开目录',exact:true}).click();
    const files=page.getByRole('button',{name:'文件',exact:true});await files.waitFor();assert.equal(await files.getAttribute('aria-expanded'),'false');
    const editor=await page.locator('.story-source-pane').boundingBox();assert.ok(editor.width>=420,JSON.stringify(editor));assert.ok(editor.height>600,JSON.stringify(editor));
    await files.click();await page.locator('.story-file-tree nav').getByRole('button',{name:'dialog.txt',exact:true}).click();
    assert.equal(await files.getAttribute('aria-expanded'),'false');await page.locator('.cm-content').getByText('First responsive line.',{exact:false}).waitFor();
    await page.screenshot({path:path.join(artifacts,'editor-portrait.png')});
    // A flow remains beside the editor when opening its referenced txt file.
    await page.setViewportSize({width:1600,height:1000});await page.locator('.story-file-tree nav').getByRole('button',{name:'A.flow',exact:true}).click();
    await page.getByRole('button',{name:'并排',exact:true}).click();await page.locator('.react-flow__node').filter({hasText:'dialog'}).click();
    await page.locator('.story-source-title').filter({hasText:'dialog.txt'}).waitFor();assert.ok(await page.locator('.react-flow__node').count()>=3);
    await page.getByRole('button',{name:'流程图',exact:true}).click();await page.locator('.react-flow__node').filter({hasText:'dialog'}).dblclick();
    assert.equal(await page.locator('.story-source-title').innerText(),'dialog.txt');assert.ok(await page.locator('.story-graph-pane').isHidden());
    await page.getByRole('button',{name:'发布',exact:true}).click();await page.locator('.story-success').waitFor();
    await page.getByRole('button',{name:'预览草稿',exact:true}).click();
    const frame=page.frameLocator('iframe[title="剧情播放器"]');await frame.locator('#textBoxMain').waitFor();
    const getRun=()=>page.evaluate(async()=>fetch('/api/ui/story/runs/'+localStorage.getItem('story-run')).then(response=>response.json()));
    const firstScene=(await getRun()).scene.token;
    await page.waitForTimeout(3000);
    assert.equal((await getRun()).scene?.token,firstScene,'autoplay must start disabled');
    // Each dialog takes at most two clicks: reveal immediately, then advance.
    const before=await getRun();const start=Date.now();
    await frame.locator('#FullScreenClick').click({position:{x:200,y:180}});
    await frame.locator('#FullScreenClick').click({position:{x:200,y:180}});
    await page.waitForFunction(async token=>{const run=await fetch('/api/ui/story/runs/'+localStorage.getItem('story-run')).then(response=>response.json());return run.scene?.token!==token;},before.scene.token,{timeout:1800});
    assert.ok(Date.now()-start<1800,'dialog advancement stalled');
    await page.waitForTimeout(250);
    await frame.locator('#textBoxMain').filter({hasText:'Second'}).waitFor();
    await page.waitForTimeout(1600); // Inspect fully revealed text, rather than the initial animation frame.
    for(const viewport of [{width:430,height:900},{width:844,height:390}]){
      await page.setViewportSize(viewport);
      const stage=await page.locator('.story-stage').boundingBox();assert.equal(stage.x,0);assert.equal(stage.y,0);assert.equal(stage.width,viewport.width);assert.equal(stage.height,viewport.height);
      assert.equal(await frame.locator('#root').evaluate(element=>getComputedStyle(element).transform),'none');
      assert.equal(await frame.locator('#pixiCanvas').evaluate(element=>getComputedStyle(element).objectFit),'contain');
      const mediaBounds=await frame.locator('#pixiCanvas').evaluate(element=>{
        const app=window.__PIXI_APP__,stage=window.PIXIapp;
        const bounds=object=>{const b=object.pixiContainer.getBounds();return {x:b.x,y:b.y,width:b.width,height:b.height};};
        return {width:app.screen.width,height:app.screen.height,backgrounds:stage.backgroundObjects.map(bounds),figures:stage.figureObjects.map(bounds)};
      });
      assert.ok(mediaBounds.backgrounds.length&&mediaBounds.figures.length,'visual resources loaded');
      for(const bounds of [...mediaBounds.backgrounds,...mediaBounds.figures]){
        assert.ok(bounds.x>=-1&&bounds.y>=-1&&bounds.x+bounds.width<=mediaBounds.width+1&&bounds.y+bounds.height<=mediaBounds.height+1,JSON.stringify(mediaBounds));
      }
      const text=await frame.locator('#textBoxMain [class*="_text_"]').boundingBox();assert.ok(text.width>100&&text.height>10,JSON.stringify(text));
      assert.equal(await frame.locator('[id="Button_ControlPanel_fast"]').count(),0);
      await page.screenshot({path:path.join(artifacts,'player-'+viewport.width+'.png')});
    }
    // Settings work in a small landscape viewport, with no bottom toolbar.
    await page.getByRole('button',{name:'菜单',exact:true}).click();await page.getByRole('button',{name:'设置',exact:true}).click();
    await frame.getByRole('dialog',{name:'设置',exact:true}).waitFor();await frame.getByRole('combobox',{name:'文字大小'}).selectOption('0');await frame.getByRole('button',{name:'关闭',exact:true}).click();
    await page.getByRole('button',{name:'菜单',exact:true}).click();await page.getByRole('button',{name:'历史',exact:true}).click();
    await frame.getByRole('dialog',{name:'历史',exact:true}).waitFor();assert.match(await frame.locator('.hosted-history').innerText(),/First responsive line/);await frame.getByRole('button',{name:'关闭',exact:true}).click();
    await page.setViewportSize({width:1280,height:800});
    await page.getByRole('button',{name:'全屏',exact:true}).click();await page.waitForFunction(()=>document.fullscreenElement?.classList.contains('story-player-shell'));
    const menu=page.getByRole('button',{name:'菜单',exact:true});
    await menu.click();await page.getByRole('button',{name:'继续',exact:true}).click();
    await frame.locator('#textBoxMain').filter({hasText:'Fourth'}).waitFor({timeout:8000});
    await menu.click();await page.getByRole('button',{name:'暂停',exact:true}).click();
    const fourthScene=(await getRun()).scene.token;
    await page.waitForTimeout(3000);
    assert.equal((await getRun()).scene?.token,fourthScene,'pausing must stop autoplay');
    for(let i=0;i<15;i++){
      const run=await getRun();if(run.status==='choice')break;
      await frame.locator('#FullScreenClick').click({position:{x:200,y:180}});await page.waitForTimeout(70);
    }
    await page.getByRole('button',{name:'Continue',exact:false}).waitFor();assert.equal(await page.evaluate(()=>document.fullscreenElement?.classList.contains('story-player-shell')),true);
    await page.getByRole('button',{name:'菜单',exact:true}).click();await page.getByRole('button',{name:'设置',exact:true}).click();
    await frame.getByRole('dialog',{name:'设置',exact:true}).waitFor();assert.ok(await page.locator('.story-choices').isHidden());
    await frame.getByRole('combobox',{name:'文字大小'}).selectOption('2');
    await frame.getByRole('button',{name:'关闭',exact:true}).click();await page.locator('.story-choices').waitFor();
    await page.waitForFunction(async()=>{const run=await fetch('/api/ui/story/runs/'+localStorage.getItem('story-run')).then(response=>response.json());return run.user_data.optionData?.textSize===2;});
    await page.screenshot({path:path.join(artifacts,'choice-fullscreen.png')});await page.locator('.story-choice').first().click();
    await page.waitForFunction(async()=>{const run=await fetch('/api/ui/story/runs/'+localStorage.getItem('story-run')).then(response=>response.json());return run.status==='text';});
    await page.getByRole('button',{name:'退出全屏',exact:true}).click();
    assert.deepEqual(errors,[]);console.log('Story responsive browser: portrait editor, graph file navigation, manual advance, autoplay controls, landscape, settings and fullscreen choice passed');
  }finally{await browser.close();if(!path.resolve(root).startsWith(path.resolve(os.tmpdir())+path.sep+'story-responsive-'))throw new Error('Unexpected fixture path');fs.rmSync(root,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
