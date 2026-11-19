const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const ts = createRequire(path.resolve(__dirname, '../../frontend/package.json'))('typescript');
const cache = new Map();
function loadTs(filename) {
  filename = path.resolve(filename);
  if(cache.has(filename)) return cache.get(filename).exports;
  const loaded = { exports: {} };cache.set(filename,loaded);
  const localRequire = createRequire(filename);
  const customRequire = name => {
    if(name.endsWith('.css'))return {};
    if(name.endsWith('?raw'))return fs.readFileSync(path.resolve(path.dirname(filename),name.slice(0,-4)),'utf8');
    if(name.startsWith('.')) {
      const target=path.resolve(path.dirname(filename),name);
      for(const extension of ['.ts','.tsx']) if(fs.existsSync(target+extension))return loadTs(target+extension);
    }
    return localRequire(name);
  };
  const compiled=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
  new Function('require','module','exports',compiled)(customRequire,loaded,loaded.exports);
  return loaded.exports;
}
module.exports={loadTs};
