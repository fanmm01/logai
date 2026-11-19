const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),os=require('node:os');
const {chromium}=require(path.resolve(__dirname,'../../../frontend/node_modules/playwright'));
const base=process.env.UI_BASE_URL||'http://127.0.0.1:8895';
(async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'story-browser-project-'));fs.writeFileSync(path.join(root,'story.project.json'),JSON.stringify({id:'editor-'+Date.now(),name:'Editor fixture'}));fs.writeFileSync(path.join(root,'A.flow'),'@START:\n->dialog <hello\n->END\n');fs.writeFileSync(path.join(root,'dialog.txt'),'Narrator:hello\n');
  const browser=await chromium.launch({channel:process.env.UI_BROWSER_CHANNEL||'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1600,height:1050}});const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(base+'/ui/story');await page.getByRole('button',{name:'创作工作区'}).click();
    await page.getByRole('textbox',{name:'工作区目录'}).fill(root);await page.getByRole('button',{name:'打开目录',exact:true}).click();
    await page.locator('.react-flow__node').first().waitFor();assert.ok(await page.locator('.react-flow__node').count()>=3);
    await page.locator('.react-flow__node').filter({hasText:'dialog'}).first().click();await page.getByRole('textbox',{name:'节点源码'}).fill('->dialog <changed');await page.getByRole('button',{name:'应用',exact:true}).click();
    await page.getByRole('button',{name:'全部保存'}).click();await page.locator('.story-success').waitFor();assert.match(fs.readFileSync(path.join(root,'A.flow'),'utf8'),/<changed/);
    await page.getByRole('textbox',{name:'新文件名'}).fill('extra.txt');await page.getByRole('button',{name:'新建文件',exact:true}).click();await page.locator('.cm-content').click();await page.keyboard.type('Narrator: new story');await page.keyboard.press('Control+s');await page.waitForTimeout(300);assert.ok(fs.existsSync(path.join(root,'extra.txt')));
    await page.getByRole('button',{name:'发布',exact:true}).click();await page.waitForTimeout(400);assert.match(await page.locator('.story-success').innerText(),/发布/);
    await page.getByRole('button',{name:'预览草稿'}).click();await page.locator('iframe[title="剧情播放器"]').waitFor();
    const run=await page.evaluate(async()=>fetch('/api/ui/story/runs/'+localStorage.getItem('story-run')).then(r=>r.json()));assert.ok(run.project.startsWith('preview-'));
    assert.equal(errors.length,0,errors.join('\n'));console.log('Story editor browser: workspace, graph-to-source, save, new file, publication and isolated preview passed');
  }finally{await browser.close();if(!path.resolve(root).startsWith(path.resolve(os.tmpdir())+path.sep+'story-browser-project-'))throw new Error('Unexpected test directory');fs.rmSync(root,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
