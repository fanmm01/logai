/** Small dependency-free hosted context; safe to import from storage/GUI code. */
export const hosted = new URLSearchParams(location.search).get('hosted') === '1';
export const hostedRun = new URLSearchParams(location.search).get('run') || '';
export const hostedBase = '/api/ui/story/runs/' + encodeURIComponent(hostedRun) + '/game/';
export const launchId = Math.random().toString(36).slice(2) + Date.now().toString(36);
export const hostState = { token: '', paused: false, loading: false, sent: '', ready: false, pendingNext: false, panel: '' };
export function emitHostEvent(type: string, payload: unknown = {}) {
  if (hosted) window.parent.postMessage({ protocol: 1, launch: launchId, type, payload }, location.origin);
}
