import {useCallback,useEffect,useRef,useState} from 'react';
import {storyApi,commandId} from './api';
import type {StoryRun} from './types';

/** A single serialized command channel shared by the player, iframe and battle UI. */
export function useStoryRun(session:string) {
  const [run,setRunState]=useState<StoryRun|null>(null);const current=useRef<StoryRun|null>(null);
  const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  const serial=useRef<Promise<unknown>>(Promise.resolve());
  const accept=useCallback((next:StoryRun)=>{
    if(current.current?.id===next.id&&current.current.revision>next.revision)return;
    current.current=next;setRunState(next);localStorage.setItem('story-run',next.id);
  },[]);
  const restore=useCallback(async(id:string)=>{const next=await storyApi<StoryRun>('/runs/'+id);if(!current.current||current.current.id===id)accept(next);return next;},[accept]);
  useEffect(()=>{if(!session)return;const id=localStorage.getItem('story-run');if(id)restore(id).catch(()=>localStorage.removeItem('story-run'));},[session,restore]);
  useEffect(()=>{
    if(!run)return;const events=new EventSource('/api/ui/story/runs/'+run.id+'/events?after='+run.revision);
    events.onmessage=e=>{try{if(current.current?.id===run.id)accept(JSON.parse(e.data));}catch{setError('剧情事件格式错误');}};
    return()=>events.close();
  },[run?.id,accept]);
  const command=useCallback((op:string,payload:unknown={})=>{
    const requestedRun=current.current?.id;
    const task=serial.current.catch(()=>{}).then(async()=>{
      const active=current.current;if(!active)throw new Error('尚未开始剧情');
      if(active.id!==requestedRun)return active;
      if(['advance','checkpoint'].includes(op)&&(payload as any).token!==active.scene?.token)return active;
      setBusy(true);setError('');const id=commandId();
      try {
        for(let attempt=0;attempt<3;attempt++){
          if(['advance','checkpoint'].includes(op)&&(payload as any).token!==current.current?.scene?.token)return current.current;
          try{const next=await storyApi<StoryRun>('/runs/'+active.id+'/commands',{commandId:id,expectedRevision:current.current!.revision,op,payload});accept(next);return next;}
          catch(e){if((e as any).status!==409||attempt===2)throw e;await restore(active.id);}
        }
      }catch(e){setError((e as Error).message);throw e;}finally{setBusy(false);}
    });
    serial.current=task;return task;
  },[accept,restore]);
  const start=useCallback(async(project:string,flow:string,entry:string)=>{
    setBusy(true);setError('');try{const next=await storyApi<StoryRun>('/runs',{project,flow,entry});accept(next);return next;}catch(e){setError((e as Error).message);throw e;}finally{setBusy(false);}
  },[accept]);
  return {run,accept,restore,command,start,error,setError,busy};
}
