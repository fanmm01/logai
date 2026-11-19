const path=require('node:path'),fs=require('node:fs');
const {chromium}=require(path.resolve(__dirname,'../frontend/node_modules/playwright'));
(async()=>{
  const browser=await chromium.launch({channel:process.env.UI_BROWSER_CHANNEL||'msedge',headless:true});const page=await browser.newPage();
  await page.setContent('<canvas width="960" height="540"></canvas>');
  const bytes=await page.evaluate(async()=>{
    const canvas=document.querySelector('canvas'),ctx=canvas.getContext('2d');const stream=canvas.captureStream(24);const parts=[];
    const recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8'});recorder.ondataavailable=e=>parts.push(e.data);const end=new Promise(resolve=>{recorder.onstop=resolve;});recorder.start();
    const start=performance.now();await new Promise(resolve=>{function draw(now){const t=(now-start)/1000;const gradient=ctx.createLinearGradient(0,0,960,540);gradient.addColorStop(0,'#3e385a');gradient.addColorStop(1,'#a48fa7');ctx.fillStyle=gradient;ctx.fillRect(0,0,960,540);ctx.strokeStyle='#e7d4b8';ctx.lineWidth=1;for(let i=0;i<5;i++){ctx.beginPath();ctx.arc(480,270,65+i*28+t*8,0,Math.PI*2);ctx.stroke();}ctx.fillStyle='#fff0d8';ctx.font='56px Georgia';ctx.textAlign='center';ctx.fillText('A LETTER FROM THE STARS',480,280);if(t<3)requestAnimationFrame(draw);else resolve();}requestAnimationFrame(draw);});
    recorder.stop();await end;return Array.from(new Uint8Array(await new Blob(parts,{type:'video/webm'}).arrayBuffer()));
  });
  const root=path.resolve(__dirname,'../story-projects/demo');fs.mkdirSync(path.join(root,'video'),{recursive:true});fs.writeFileSync(path.join(root,'opening.webm'),Buffer.from(bytes));fs.writeFileSync(path.join(root,'video/transition.webm'),Buffer.from(bytes));await browser.close();console.log('Generated story demo videos');
})().catch(e=>{console.error(e);process.exit(1);});
