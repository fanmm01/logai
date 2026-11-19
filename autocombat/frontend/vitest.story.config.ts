import {defineConfig} from 'vitest/config';
export default defineConfig({test:{include:['../dev/frontend-tests/story/**/*.test.ts'],environment:'node',coverage:{provider:'v8',include:['src/story/bridge.ts','src/story/graph.ts','src/story/api.ts','src/story/assets.ts'],reporter:['text','json'],reportsDirectory:'../.tmpwork/story-frontend-coverage',thresholds:{lines:85,branches:80}}}});
