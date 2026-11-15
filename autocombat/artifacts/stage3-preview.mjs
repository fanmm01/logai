// Preview the human-written candidate without changing the published JSON.
import { createServer } from '../frontend/node_modules/vite/dist/node/index.js';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const profiles = JSON.parse(fs.readFileSync(path.join(root, 'frontend/src/data/encyclopedia.json'), 'utf8'));
const proposal = JSON.parse(fs.readFileSync(path.join(root, 'docs/stage3-prose-proposed.json'), 'utf8'));
for (const [serial, patch] of Object.entries(proposal)) {
  if (patch.passives) profiles[serial].passives = patch.passives;
  for (const article of patch.skills || []) {
    Object.assign(profiles[serial].skills.find(a => a.name === article.name && a.phase === article.phase), article);
  }
}
const server = await createServer({
  configFile: false, root: path.join(root, 'frontend'), base: '/ui/',
  plugins: [{name: 'stage3-prose-review', enforce: 'pre', load(id) {
    if (id.replaceAll('\\', '/').endsWith('/src/data/encyclopedia.json')) return JSON.stringify(profiles);
  }}],
  server: {host: '127.0.0.1', port: 8891, strictPort: true,
    proxy: {'/api': {target: 'http://127.0.0.1:8889', changeOrigin: true,
      configure(proxy) {proxy.on('proxyReq', req => req.setHeader('Origin', 'http://127.0.0.1:8889'));}}}},
});
await server.listen();
server.printUrls();
