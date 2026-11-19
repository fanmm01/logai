export type EngineMessage={type:string;protocol:number;launch:string;requestId?:string;payload?:any;error?:string};
export function createHostBridge(frame:()=>HTMLIFrameElement|null,onEvent:(type:string,payload:any)=>void) {
  let launch='';let disposed=false;
  const pending=new Map<string,{resolve:(x:any)=>void;reject:(x:Error)=>void;timer:ReturnType<typeof setTimeout>}>();
  const listener=(event:MessageEvent)=>{
    if(disposed||event.origin!==location.origin||event.source!==frame()?.contentWindow)return;
    const data=event.data as EngineMessage;
    if(!data||data.protocol!==1||typeof data.type!=='string')return;
    if(data.type==='ready'){launch=data.launch;onEvent('ready',{});return;}
    if(data.launch!==launch)return;
    if(data.type==='reply'&&data.requestId){const p=pending.get(data.requestId);if(p){clearTimeout(p.timer);pending.delete(data.requestId);data.error?p.reject(new Error(data.error)):p.resolve(data.payload);}return;}
    onEvent(data.type,data.payload);
  };
  window.addEventListener('message',listener);
  return {
    send(type:string,payload:any={}):Promise<any>{
      const id=globalThis.crypto?.randomUUID?.()||Math.random().toString(36);
      return new Promise((resolve,reject)=>{
        if(!launch||disposed){reject(new Error('剧情引擎尚未就绪'));return;}
        const timer=setTimeout(()=>{pending.delete(id);reject(new Error('剧情引擎响应超时'));},15000);
        pending.set(id,{resolve,reject,timer});frame()?.contentWindow?.postMessage({type,protocol:1,launch,requestId:id,payload},location.origin);
      });
    },
    dispose(){disposed=true;window.removeEventListener('message',listener);for(const p of pending.values()){clearTimeout(p.timer);p.reject(new Error('剧情引擎已关闭'));}pending.clear();}
  };
}
