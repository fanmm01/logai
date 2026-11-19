import {it,expect,vi,afterEach} from 'vitest';
import {createHostBridge} from '../../../frontend/src/story/bridge';
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();});
function setup(){
  let listener:(e:any)=>void=()=>{};const child={postMessage:vi.fn()};const events=vi.fn();
  vi.stubGlobal('location',{origin:'http://local'});vi.stubGlobal('window',{addEventListener:(_:string,fn:any)=>{listener=fn;},removeEventListener:vi.fn()});
  const frame={contentWindow:child} as any;const bridge=createHostBridge(()=>frame,events);
  const send=(data:any,origin='http://local',source:any=child)=>listener({data,origin,source});
  return {bridge,send,events,child};
}
it('requires handshake and validates source, origin, protocol and launch',async()=>{
  const x=setup();await expect(x.bridge.send('capture')).rejects.toThrow();
  x.send({type:'ready',protocol:1,launch:'a'},'http://other');x.send({type:'ready',protocol:1,launch:'a'},'http://local',{});x.send({type:'ready',protocol:2,launch:'a'});x.send(null);expect(x.events).not.toHaveBeenCalled();
  x.send({type:'ready',protocol:1,launch:'a'});expect(x.events).toHaveBeenCalledWith('ready',{});
  x.send({type:'advance',protocol:1,launch:'old',payload:{}});expect(x.events).toHaveBeenCalledTimes(1);
  x.send({type:'advance',protocol:1,launch:'a',payload:{token:'t'}});expect(x.events).toHaveBeenCalledWith('advance',{token:'t'});x.bridge.dispose();
});
it('matches request replies, errors and timeouts',async()=>{
  vi.useFakeTimers();const x=setup();x.send({type:'ready',protocol:1,launch:'a'});
  const request=x.bridge.send('capture');const id=x.child.postMessage.mock.calls[0][0].requestId;x.send({type:'reply',protocol:1,launch:'a',requestId:id,payload:{ok:true}});await expect(request).resolves.toEqual({ok:true});
  const failed=x.bridge.send('capture');const failedId=x.child.postMessage.mock.calls[1][0].requestId;x.send({type:'reply',protocol:1,launch:'a',requestId:failedId,error:'bad'});await expect(failed).rejects.toThrow('bad');
  const timeout=x.bridge.send('capture');const assertion=expect(timeout).rejects.toThrow('超时');await vi.advanceTimersByTimeAsync(15000);await assertion;x.bridge.dispose();
});
it('cleans up pending calls when the iframe is destroyed',async()=>{
  const x=setup();x.send({type:'ready',protocol:1,launch:'a'});const pending=x.bridge.send('capture');x.bridge.dispose();await expect(pending).rejects.toThrow('关闭');await expect(x.bridge.send('capture')).rejects.toThrow();
});
