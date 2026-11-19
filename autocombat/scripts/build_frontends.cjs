// One entry point for source checkouts, launcher, restart and release builds.
const fs=require('node:fs'),path=require('node:path'),{spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');const npmCli=process.env.npm_execpath||path.join(path.dirname(process.execPath),'node_modules/npm/bin/npm-cli.js');
function run(args,cwd){const result=spawnSync(process.execPath,[npmCli,...args],{cwd,stdio:'inherit'});if(result.error)throw result.error;if(result.status)process.exit(result.status);}
const app=path.join(root,'frontend'),engine=path.join(root,'webgal-adv/engine');
run(['run','build:app'],app);
if(!fs.existsSync(path.join(engine,'node_modules/typescript')))run(['ci','--no-audit','--no-fund'],engine);
run(['run','build'],engine);
const engineDist=path.join(engine,'packages/webgal/dist');
fs.cpSync(engineDist,path.join(app,'dist/story-engine'),{recursive:true,filter:source=>source!==path.join(engineDist,'game')});
for(const file of ['index.html','story-engine/index.html'])if(!fs.existsSync(path.join(app,'dist',file)))throw new Error('Missing frontend output: '+file);
