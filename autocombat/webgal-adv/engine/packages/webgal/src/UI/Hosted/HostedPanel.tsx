import { useEffect, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { RootState, webgalStore } from '@/store/store';
import { setOptionData } from '@/store/userDataReducer';
import { hostState, emitHostEvent } from '@/Core/hosted/context';
import { WebGAL } from '@/Core/WebGAL';

/** Compact history and preferences for the responsive hosted stage. */
export function HostedPanel() {
  const [panel, setPanel] = useState('');
  const closeButton = useRef<HTMLButtonElement>(null);
  const options = useSelector((state: RootState) => state.userData.optionData);
  const dispatch = useDispatch();
  const close = () => {
    if (hostState.panel === 'settings') emitHostEvent('preferences', { user_data: webgalStore.getState().userData });
    hostState.panel = ''; setPanel(''); emitHostEvent('panelClosed');
  };
  useEffect(() => {
    const show = (event: Event) => setPanel((event as CustomEvent<string>).detail);
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') close(); };
    window.addEventListener('story-panel', show);
    window.addEventListener('keydown', escape);
    return () => { window.removeEventListener('story-panel', show); window.removeEventListener('keydown', escape); };
  }, []);
  useEffect(() => { if (panel) closeButton.current?.focus(); }, [panel]);
  if (!panel) return null;
  const history = WebGAL.backlogManager.getBacklog();
  return <div className="hosted-panel-scrim" onClick={close}>
    <section className="hosted-panel" role="dialog" aria-modal="true" aria-label={panel === 'history' ? '历史' : '设置'} onClick={event => event.stopPropagation()}>
      <header><h2>{panel === 'history' ? '阅读历史' : '阅读设置'}</h2><button ref={closeButton} onClick={close}>关闭</button></header>
      {panel === 'history' ? <div className="hosted-history">{history.map((item, index) => <article key={index}><strong>{item.currentStageState.showName}</strong><p>{item.currentStageState.showText.replace(/\|/g, '\n')}</p></article>)}{!history.length && <p>还没有阅读记录。</p>}</div> : <div className="hosted-preferences">
        {([['textSpeed', '文字速度'], ['volumeMain', '主音量'], ['bgmVolume', '音乐音量']] as const).map(([key, label]) => <label key={key}>{label}<output>{options[key]}</output><input aria-label={label} type="range" min="0" max="100" value={options[key]} onChange={event => dispatch(setOptionData({ key, value: Number(event.target.value) }))}/></label>)}
        <label>文字大小<select aria-label="文字大小" value={options.textSize} onChange={event => dispatch(setOptionData({ key: 'textSize', value: Number(event.target.value) }))}><option value="0">小</option><option value="1">标准</option><option value="2">大</option></select></label>
      </div>}
    </section>
  </div>;
}
