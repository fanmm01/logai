import {useEffect,useRef,useState} from 'react';
import {ReactFlow,Background,Controls,MiniMap,useNodesState} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type {Graph} from './types';
import {layoutGraph,connectSource} from './graph';
export function FlowEditor({graph,source,onChange,onFocus,onPositions,positions,onError,readOnly=false,onOpenFile}:{graph:Graph;source:string;onChange:(source:string)=>void;onFocus:(line:number)=>void;onPositions:(p:Record<string,{x:number;y:number}>)=>void;positions:Record<string,{x:number;y:number}>;onError:(text:string)=>void;readOnly?:boolean;onOpenFile?:(path:string,doubleClick:boolean)=>void}) {
  const [nodes,setNodes,onNodesChange]=useNodesState<any>([]),[edges,setEdges]=useState<any[]>([]),[selected,setSelected]=useState<string|null>(null),[snippet,setSnippet]=useState('');
  const canvas=useRef<HTMLDivElement>(null),flow=useRef<{fitView:(options?:any)=>any}|null>(null);
  useEffect(()=>{
    if(!canvas.current)return;let timer=0,width=0,height=0;
    const observer=new ResizeObserver(()=>{
      const nextWidth=canvas.current?.clientWidth||0,nextHeight=canvas.current?.clientHeight||0;
      if(!nextWidth||!nextHeight)return;
      const fit=!width||Math.abs(width-nextWidth)>50||Math.abs(height-nextHeight)>160;
      width=nextWidth;height=nextHeight;
      if(fit){cancelAnimationFrame(timer);timer=requestAnimationFrame(()=>void flow.current?.fitView({padding:.2}));}
    });
    observer.observe(canvas.current);return()=>{observer.disconnect();cancelAnimationFrame(timer);};
  },[]);
  useEffect(()=>{const next=layoutGraph(graph,positions);setNodes(next.nodes);setEdges(next.edges);},[graph]);
  const selection=graph.nodes.find(n=>n.id===selected);
  useEffect(()=>{if(selection?.start!=null&&selection.end!=null)setSnippet(source.slice(selection.start,selection.end));},[selected,source]);
  const replace=()=>{if(selection?.start!=null&&selection.end!=null)onChange(source.slice(0,selection.start)+snippet+source.slice(selection.end));};
  const remove=()=>{if(selection?.start!=null&&selection.end!=null){onChange(source.slice(0,selection.start)+source.slice(selection.end));setSelected(null);}};
  return <div className="story-graph-shell"><div ref={canvas} className="story-flow-canvas"><ReactFlow onInit={instance=>{flow.current=instance;}} nodes={nodes} edges={edges} fitView onNodesChange={onNodesChange} onNodeDragStop={(_,node)=>onPositions({...positions,[node.id]:node.position})} onNodeClick={(_,node)=>{setSelected(node.id);if(node.data.targetPath)onOpenFile?.(String(node.data.targetPath),false);else onFocus(Number(node.data.line));}} onNodeDoubleClick={(_,node)=>{if(node.data.targetPath)onOpenFile?.(String(node.data.targetPath),true);}} onConnect={connection=>{if(readOnly)return;try{onChange(connectSource(source,graph,connection.source,connection.target));}catch(e){onError((e as Error).message);}}}><Background color="#cfc8dd" gap={22}/><Controls/><MiniMap pannable zoomable/></ReactFlow></div><aside className="story-node-properties"><div><strong>节点属性</strong><span>{selection?.kind||'选择节点'}</span></div>{selection?.start!=null?<><textarea aria-label="节点源码" value={snippet} onChange={e=>setSnippet(e.target.value)}/><div><button className="button" onClick={replace}>应用</button><button className="button subtle" onClick={remove}>删除节点</button></div></>:<p>并排时单击文本节点打开文件；双击可从任意模式打开。拖动调整布局，连线修改跳转。</p>}{!readOnly&&<><label>新增语句<textarea aria-label="新增流程语句" defaultValue="->chapter1" id="story-new-statement"/></label><button className="button" onClick={()=>{const input=document.getElementById('story-new-statement') as HTMLTextAreaElement;onChange(source.trimEnd()+'\n'+input.value+'\n');}}>添加节点</button></>}<small>入口示例：@XU.CONTINUE-A||START<br/>权限示例：UNLOCK@crossroads[1]</small></aside></div>;
}
