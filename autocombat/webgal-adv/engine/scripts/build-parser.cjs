// Build on Windows too: avoid the upstream rollup TS filter's platform-dependent glob.
const fs=require('node:fs');const path=require('node:path');const ts=require('typescript');const esbuild=require('esbuild');
const root=path.resolve(__dirname,'../packages/parser');
(async()=>{
  const config=ts.readConfigFile(path.join(root,'tsconfig.json'),ts.sys.readFile);
  const parsed=ts.parseJsonConfigFileContent(config.config,ts.sys,root,{noEmit:false,emitDeclarationOnly:true,declaration:true,skipLibCheck:true,declarationDir:path.join(root,'build/types')});
  const program=ts.createProgram(parsed.fileNames,parsed.options);const result=program.emit();
  const diagnostics=ts.getPreEmitDiagnostics(program).concat(result.diagnostics);
  if(diagnostics.some(x=>x.category===ts.DiagnosticCategory.Error)){console.error(ts.formatDiagnosticsWithColorAndContext(diagnostics,{getCurrentDirectory:()=>root,getCanonicalFileName:x=>x,getNewLine:()=> '\n'}));process.exit(1);}
  for(const [format,out] of [['esm','es/index.js'],['cjs','cjs/index.cjs'],['iife','umd/index.global.js']])await esbuild.build({entryPoints:[path.join(root,'src/index.ts')],bundle:true,format,globalName:format==='iife'?'webgalParser':undefined,outfile:path.join(root,'build',out),platform:'browser',target:'es2020'});
})().catch(e=>{console.error(e);process.exit(1);});
