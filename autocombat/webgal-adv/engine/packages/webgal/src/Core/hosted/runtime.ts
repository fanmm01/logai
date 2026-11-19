import { hosted, hostedRun, hostedBase, hostState, launchId, emitHostEvent } from './context';
import { WebGAL } from '@/Core/WebGAL';
import PixiStage from '@/Core/controller/stage/pixi/PixiController';
import { syncPixiStageState } from '@/Core/controller/stage/pixi/syncPixiStageState';
import { stageStateManager } from '@/Core/Modules/stage/stageStateManager';
import { webgalStore } from '@/store/store';
import { setVisibility } from '@/store/GUIReducer';
import { resetUserData, initState } from '@/store/userDataReducer';
import { generateCurrentStageData } from '@/Core/controller/storage/saveGame';
import { sceneFetcher } from '@/Core/controller/scene/sceneFetcher';
import { sceneParser } from '@/Core/parser/sceneParser';
import { stopAllPerform } from '@/Core/controller/gamePlay/stopAllPerform';
import { switchAuto } from '@/Core/controller/gamePlay/autoPlay';
import { forward, commitForward, nextSentence } from '@/Core/controller/gamePlay/nextSentence';
import { restorePerform } from '@/Core/controller/storage/jumpFromBacklog';
import cloneDeep from 'lodash/cloneDeep';
import { saveActions } from '@/store/savesReducer';
import '@/UI/Hosted/hosted.scss';

let checkpointTimer: ReturnType<typeof setTimeout> | undefined;
let slotsLoaded = false;
let autoPlayEnabled = false;
function syncHostedAutoPlay() {
  const shouldPlay = autoPlayEnabled && !hostState.paused && !hostState.loading && !hostState.panel
    && hostState.token !== hostState.sent;
  if (WebGAL.gameplay.isAuto !== shouldPlay) switchAuto();
}
function mediaState() {
  return Array.from(document.querySelectorAll('audio,video')).map((element, index) => {
    const e = element as HTMLMediaElement;
    return { index, src: e.currentSrc || e.src, time: e.currentTime, paused: e.paused };
  });
}
export function captureHostedCheckpoint() {
  const snapshot = generateCurrentStageData(-1, false);
  return { ...snapshot, hostToken: hostState.token, media: mediaState() };
}
function userData() { return cloneDeep(webgalStore.getState().userData); }
async function waitForStableStage() {
  if (WebGAL.sceneManager.sceneWritePromise) await WebGAL.sceneManager.sceneWritePromise;
  if (WebGAL.sceneManager.lockSceneWrite) throw new Error('场景仍在切换，请稍后重试存档');
}
function hydrateStage(payload: any) {
  if (payload.userData && Object.keys(payload.userData).length) webgalStore.dispatch(resetUserData({ ...cloneDeep(initState), ...payload.userData }));
  if (payload.snapshot) {
    stopAllPerform();
    stageStateManager.replaceCalculationStageState(cloneDeep(payload.snapshot.nowStageState));
    stageStateManager.removeAllPerform();
    stageStateManager.commit({ skipAnimation: true });
  }
}
export function reportCheckpoint() {
  if (!hostState.ready || hostState.loading || !hostState.token || WebGAL.sceneManager.lockSceneWrite) return;
  emitHostEvent('checkpoint', { token: hostState.token, snapshot: captureHostedCheckpoint(), user_data: userData() });
}
export function completeHostedBlock() {
  if (hostState.loading || hostState.sent === hostState.token) return;
  hostState.sent = hostState.token;
  syncHostedAutoPlay();
  clearTimeout(checkpointTimer);
  emitHostEvent('advance', { token: hostState.token, snapshot: captureHostedCheckpoint(), user_data: userData() });
}
export async function loadHostedScene(payload: any) {
  if (!payload.scene || payload.scene.token === hostState.token) return;
  hostState.loading = true;
  syncHostedAutoPlay();
  try {
    await waitForStableStage();
    stopAllPerform();
    WebGAL.gameplay.resetGamePlay();
    hostState.token = payload.scene.token; hostState.sent = '';
    if (payload.userData && Object.keys(payload.userData).length) webgalStore.dispatch(resetUserData({ ...cloneDeep(initState), ...payload.userData }));
    const snapshot = payload.snapshot;
    const slots = slotsLoaded ? { slots: [] } : await fetch('/api/ui/story/runs/' + hostedRun + '/slots').then(r => r.json()).catch(() => ({ slots: [] }));
    slotsLoaded = true;
    for (const slot of slots.slots || []) {
      const index = slot.slot === 'quick' ? 0 : Number(slot.slot);
      if (slot.snapshot && Number.isInteger(index) && index >= 0) webgalStore.dispatch(saveActions.saveGame({ index, saveData: { ...slot.snapshot, index, saveTime: new Date(slot.time * 1000).toLocaleString() } }));
    }
    if (snapshot) {
      stageStateManager.replaceCalculationStageState(cloneDeep(snapshot.nowStageState));
      const backlog = WebGAL.backlogManager.getBacklog(); backlog.splice(0, backlog.length, ...cloneDeep(snapshot.backlog || []));
    }
    const url = hostedBase + 'scene/' + payload.scene.token + '.txt';
    const restoring = snapshot && snapshot.hostToken === payload.scene.token;
    const sceneUrl = restoring ? snapshot.sceneData.sceneUrl : url;
    const script = sceneUrl === url && typeof payload.scene.script === 'string' ? payload.scene.script : await sceneFetcher(sceneUrl);
    WebGAL.sceneManager.sceneData.currentScene = sceneParser(script, payload.scene.path, sceneUrl);
    WebGAL.sceneManager.sceneData.currentSentenceId = restoring ? snapshot.sceneData.currentSentenceId : 0;
    WebGAL.sceneManager.sceneData.currentLocals = restoring ? cloneDeep(snapshot.sceneData.currentLocals || {}) : {};
    WebGAL.sceneManager.sceneData.sceneStack = restoring ? cloneDeep(snapshot.sceneData.sceneStack) : [];
    WebGAL.sceneManager.settledScenes.add(sceneUrl);
    // Flow variables are stage variables too, so native interpolation/conditions work.
    for (const [key, value] of Object.entries(payload.variables || {})) stageStateManager.setStageVar({ key, value: value as any });
    if (snapshot && restoring) restorePerform(true);
    if (!restoring) {
      // Remove the previous scene's blocking host boundary without clearing stage assets.
      WebGAL.gameplay.performController.unmountPerform('story-host');
      stageStateManager.removeAllPerform();
      forward(); commitForward();
    }
    if (restoring && snapshot.media) {
      setTimeout(() => {
        document.querySelectorAll('audio,video').forEach((e, i) => {
          const media = e as HTMLMediaElement; const saved = snapshot.media.find((x: any) => x.index === i);
          if (!saved) return;
          const seek = () => { if (Number.isFinite(media.duration)) media.currentTime = Math.min(saved.time, media.duration); };
          if (media.readyState >= 1) seek(); else media.addEventListener('loadedmetadata', seek, { once: true });
        });
      }, 300);
    }
  } finally {
    hostState.loading = false;
    if (hostState.pendingNext && !hostState.paused && !hostState.panel) {
      hostState.pendingNext = false;
      requestAnimationFrame(() => nextSentence());
    }
  }
  syncHostedAutoPlay();
}
export function initializeHostedRuntime() {
  document.documentElement.classList.add('story-hosted');
  WebGAL.gameKey = 'story-' + hostedRun;
  WebGAL.gameplay.pixiStage = new PixiStage();
  stageStateManager.setCommitHandler((state, options) => {
    syncPixiStageState(state, options);
    if (options.notifyReact && !hostState.loading) {
      clearTimeout(checkpointTimer); checkpointTimer = setTimeout(reportCheckpoint, 250);
    }
  });
  for (const component of ['showStarter','showTitle','showMenuPanel','isShowLogo'] as const) webgalStore.dispatch(setVisibility({ component, visibility: false }));
  webgalStore.dispatch(setVisibility({ component: 'isEnterGame', visibility: true }));
  window.addEventListener('message', async event => {
    const message = event.data;
    if (event.origin !== location.origin || event.source !== window.parent || !message || message.protocol !== 1 || message.launch !== launchId) return;
    let payload: unknown = {}; let error: string | undefined;
    try {
      if (message.type === 'load') await loadHostedScene(message.payload);
      else if (message.type === 'hydrate') { await waitForStableStage(); hydrateStage(message.payload); }
      else if (message.type === 'capture') { await waitForStableStage(); payload = { snapshot: captureHostedCheckpoint(), user_data: userData() }; }
      else if (message.type === 'pause') {
        hostState.paused = !!message.payload.paused;
        if (hostState.paused) hostState.pendingNext = false;
        if (hostState.paused) WebGAL.gameplay.resetGamePlay();
        syncHostedAutoPlay();
        document.querySelectorAll('audio,video').forEach(e => {
          const media = e as HTMLMediaElement;
          if (message.payload.mute) media.pause();
          else if (media.src) void media.play().catch(() => {});
        });
      } else if (message.type === 'autoPlay') {
        autoPlayEnabled = !!message.payload.enabled;
        syncHostedAutoPlay();
      } else if (message.type === 'panel') {
        hostState.panel = message.payload.panel || '';
        syncHostedAutoPlay();
        window.dispatchEvent(new CustomEvent('story-panel', { detail: hostState.panel }));
      } else if (message.type === 'save') emitHostEvent('save', { slot: message.payload.slot, snapshot: captureHostedCheckpoint() });
    } catch (e) { error = String(e); emitHostEvent('error', { message: error }); }
    window.parent.postMessage({ protocol: 1, launch: launchId, type: 'reply', requestId: message.requestId, payload, error }, location.origin);
  });
  hostState.ready = true;
  fetch(hostedBase + 'animation/animationTable.json').then(x => x.json()).then(async (names: string[]) => {
    for (const name of names) WebGAL.animationManager.addAnimation(name, await fetch(hostedBase + 'animation/' + name + '.json').then(x => x.json()));
  }).catch(() => {});
  emitHostEvent('ready');
}
