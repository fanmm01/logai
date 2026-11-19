import pageCopy from '../data/pageCopy.json';
import {useEffect,useRef,useState,forwardRef,useImperativeHandle,type ReactNode} from 'react';
import {ArrowLeft,Save,FolderOpen,RotateCcw,ArrowRight,Lock,Play,Pause,Menu,Maximize,Minimize,History,Settings,X} from 'lucide-react';
import {createHostBridge} from './bridge';
import type {StoryRun,Chapter} from './types';
export type PlayerHandle={capture:()=>Promise<any>};
export const PlayerPage=forwardRef<PlayerHandle,{run:StoryRun;visible:boolean;inBattle:boolean;busy:boolean;command:(op:string,payload?:any)=>Promise<any>;onBack:()=>void;onContinue:(chapter:Chapter,entry:string)=>void;onBattle:()=>void;onError:(message:string)=>void;encounter?:ReactNode;error?:string}>(function PlayerPage({run,visible,inBattle,busy,command,onBack,onContinue,onBattle,onError,encounter,error},ref){
  const frame=useRef<HTMLIFrameElement>(null);const bridge=useRef<ReturnType<typeof createHostBridge>|null>(null);const [ready,setReady]=useState(false);const [autoPlay,setAutoPlay]=useState(false);
  const runRef=useRef(run);runRef.current=run;const commandRef=useRef(command);commandRef.current=command;const sent=useRef('');
  const shell=useRef<HTMLDivElement>(null);const [menuOpen,setMenuOpen]=useState(false),[fullscreen,setFullscreen]=useState(false),[enginePanel,setEnginePanel]=useState(false);
  useEffect(()=>{const changed=()=>setFullscreen(!!document.fullscreenElement);document.addEventListener('fullscreenchange',changed);return()=>document.removeEventListener('fullscreenchange',changed);},[]);
  useEffect(()=>{if(!visible&&document.fullscreenElement===shell.current)void document.exitFullscreen().catch(()=>{});if(!visible)setMenuOpen(false);},[visible]);
  const toggleFullscreen=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await shell.current?.requestFullscreen();}catch(e){onError((e as Error).message);}};
  useImperativeHandle(ref,()=>({capture:async()=>ready?bridge.current?.send('capture'):null}),[ready]);
  useEffect(()=>{
    sent.current='';setReady(false);setAutoPlay(false);
    const channel=createHostBridge(()=>frame.current,(type,payload)=>{
      if(type==='ready'){setReady(true);return;}
      if(type==='panelClosed'){setEnginePanel(false);return;}
      if(type==='chapters'){onBack();return;}
      if(type==='error'){onError(payload?.message||'剧情引擎错误');return;}
      if(['advance','checkpoint'].includes(type)){
        if(runRef.current.status!=='text'||payload.token!==runRef.current.scene?.token)return;
        void commandRef.current(type,payload).catch(()=>{});
      }else if(['save','load','preferences'].includes(type))void commandRef.current(type,payload).catch(()=>{});
    });bridge.current=channel;
    return()=>{channel.dispose();bridge.current=null;};
  },[run.id]);
  useEffect(()=>{
    if(!ready||!run.scene||run.status!=='text'||sent.current===run.scene.token)return;
    sent.current=run.scene.token;
    void bridge.current?.send('load',{scene:run.scene,snapshot:run.player_snapshot,userData:run.user_data,variables:(run as any).variables}).catch(e=>{sent.current='';onError(e.message);});
  },[ready,run.scene?.token,run.status]);
  useEffect(()=>{if(ready&&run.status!=='text'&&!sent.current){sent.current='stage-only';void bridge.current?.send('hydrate',{snapshot:run.player_snapshot,userData:run.user_data}).catch(e=>onError(e.message));}},[ready,run.status]);
  useEffect(()=>{if(ready)void bridge.current?.send('pause',{paused:!visible||run.status!=='text',mute:!visible&&!inBattle}).catch(()=>{});},[ready,visible,inBattle,run.status]);
  useEffect(()=>{if(ready)void bridge.current?.send('autoPlay',{enabled:autoPlay}).catch(e=>onError(e.message));},[ready,autoPlay]);
  const save=async()=>{const checkpoint=ready?await bridge.current?.send('capture'):null;await command('save',{slot:'quick',snapshot:checkpoint?.snapshot});};
  useEffect(()=>{if(!visible&&ready){setEnginePanel(false);void bridge.current?.send('panel',{panel:''}).catch(()=>{});}},[visible,ready]);
  const openPanel=(panel:string)=>{setMenuOpen(false);setEnginePanel(true);void bridge.current?.send('panel',{panel}).catch(e=>{setEnginePanel(false);onError(e.message);});};
  return <div ref={shell} className="story-player-shell" hidden={!visible}>{error&&<div className="story-player-error" role="alert"><span>{error}</span><button aria-label="关闭提示" onClick={()=>onError('')}><X size={16}/></button></div>}<div className="story-player-toolbar"><button className="button subtle" onClick={onBack}><ArrowLeft size={16}/>章节列表</button><span>{run.flow.replace('.flow','')}</span><div>{!!document.fullscreenEnabled&&<button className="button subtle compact-button" aria-label={fullscreen?'退出全屏':'全屏'} onClick={()=>void toggleFullscreen()}>{fullscreen?<Minimize size={16}/>:<Maximize size={16}/>}</button>}<button className="button subtle compact-button" aria-expanded={menuOpen} aria-controls="story-player-menu" onClick={()=>setMenuOpen(x=>!x)}><Menu size={16}/>菜单</button></div></div>{menuOpen&&<><button className="story-menu-dismiss" aria-label="关闭菜单" onClick={()=>setMenuOpen(false)}/><div id="story-player-menu" className="story-player-menu"><button className="button subtle" disabled={busy||!ready} onClick={()=>{setAutoPlay(x=>!x);setMenuOpen(false);}}>{autoPlay?<Pause size={15}/>:<Play size={15}/>} {autoPlay?'暂停':'继续'}</button><button className="button subtle" disabled={busy} onClick={()=>void save().then(()=>setMenuOpen(false)).catch(e=>onError(e.message))}><Save size={15}/>存档</button><button className="button subtle" disabled={busy||!run.slots.some(x=>x.slot==='quick')} onClick={()=>void command('load',{slot:'quick'}).then(()=>setMenuOpen(false)).catch(()=>{})}><FolderOpen size={15}/>读档</button><button className="button subtle" disabled={!ready} onClick={()=>openPanel('history')}><History size={15}/>历史</button><button className="button subtle" disabled={!ready} onClick={()=>openPanel('settings')}><Settings size={15}/>设置</button></div></>}<div className="story-stage"><iframe key={run.id} ref={frame} title="剧情播放器" src={'/ui/story-engine/?hosted=1&run='+run.id} allow="autoplay; fullscreen"/><div inert={enginePanel} className={'story-stage-shade '+(enginePanel?'engine-panel-open ':'')+(['choice','retry','ended','continue','paused','battle_ready','battle','external_ready','external_error','external_running'].includes(run.status)?'active':'')}>
    {!ready&&<div className="story-state-card"><span className="story-spinner"/>{pageCopy.storyPlayer.loading}</div>}
    {run.status==='choice'&&<section className="story-choices" aria-label="剧情选择">{run.choice?.options.map(option=><button className="story-choice" key={option.id} disabled={!option.enabled||busy} onClick={()=>void command('choose',{id:option.id}).catch(()=>{})}><span>{option.enabled?option.id:<Lock size={16}/>}</span>{option.text}<ArrowRight size={16}/></button>)}{run.choice?.options.every(o=>!o.enabled)&&<p>所有选项已锁定。可以读档或返回章节列表。</p>}</section>}
    {run.status==='retry'&&<section className="story-state-card"><small>{pageCopy.storyPlayer.retryEyebrow}</small><h2>{pageCopy.storyPlayer.retryTitle}</h2><p>{run.message}</p><button className="button primary" disabled={busy} onClick={()=>void command('retry').catch(()=>{})}><RotateCcw size={16}/>重试</button></section>}
    {run.status==='continue'&&<section className="story-state-card"><small>{pageCopy.storyPlayer.continueEyebrow}</small><h2>{pageCopy.storyPlayer.continueTitle}</h2><p>{run.message}</p>{run.successors?.filter(c=>c.unlocked).flatMap(c=>c.entries.filter(e=>e.kind==='continue').map(e=><button className="button primary" key={c.path+e.id} onClick={()=>onContinue(c,e.id)}>{c.title} · {e.label}<ArrowRight size={16}/></button>))}<button className="button" onClick={onBack}>返回章节列表</button></section>}
    {run.status==='ended'&&<section className="story-state-card"><small>{pageCopy.storyPlayer.completeEyebrow}</small><h2>{pageCopy.storyPlayer.completeTitle}</h2><p>本章已完成，进度已经保存。</p><button className="button primary" onClick={onBack}>返回章节列表 <ArrowRight size={16}/></button></section>}
    {run.status==='battle_ready'&&encounter}
    {run.status==='battle'&&<section className="story-state-card"><h2>{pageCopy.storyPlayer.battleTitle}</h2><p>中途状态已自动保存。服务重启后可恢复到同一行动。</p><button className="button primary" onClick={onBattle}>进入战术室 <ArrowRight size={16}/></button></section>}
    {run.status.startsWith('external_')&&<section className="story-state-card"><h2>外部游戏规则</h2><p>{run.message||'准备将数据交给规则适配器。'}</p>{run.status==='external_running'&&<button className="button" onClick={()=>void command('cancelExternal').catch(()=>{})}>取消执行</button>}{run.status!=='external_running'&&<button className="button primary" onClick={()=>void command('executeExternal').catch(()=>{})}>执行规则</button>}</section>}
    {run.status==='paused'&&<section className="story-state-card"><h2>剧情已暂停</h2><p>{run.message}</p><button className="button" onClick={()=>void command('retry').catch(()=>{})}>回到分支入口</button></section>}
  </div></div></div>;
});
