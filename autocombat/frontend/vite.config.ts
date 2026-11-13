import { defineConfig } from 'vite';
export default defineConfig({
  base: '/ui/',
  server: { proxy: { '/api': {
    target: 'http://127.0.0.1:8889', changeOrigin: true,
    configure: proxy => { proxy.on('proxyReq', req => req.setHeader('Origin', 'http://127.0.0.1:8889')); },
  } } },
  build: { outDir: 'dist', emptyOutDir: true },
});
