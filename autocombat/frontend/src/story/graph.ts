import dagre from '@dagrejs/dagre';
import type {Graph} from './types';
export function layoutGraph(graph:Graph,positions:Record<string,{x:number;y:number}>={}) {
  const layout=new dagre.graphlib.Graph();layout.setGraph({rankdir:'TB',ranksep:65,nodesep:35});layout.setDefaultEdgeLabel(()=>({}));
  graph.nodes.forEach(n=>layout.setNode(n.id,{width:210,height:78}));graph.edges.forEach(e=>layout.setEdge(e.source,e.target));dagre.layout(layout);
  return {nodes:graph.nodes.map(n=>({id:n.id,type:'default',position:positions[n.id]||{x:layout.node(n.id).x,y:layout.node(n.id).y},data:{label:n.label,kind:n.kind,line:n.line,targetPath:n.targetPath},className:'story-flow-node kind-'+n.kind})),edges:graph.edges.map(e=>({...e,animated:e.label==='RETRY'}))};
}
export function projectGraph(graphs:Record<string,Graph>):Graph {
  const nodes=Object.entries(graphs).map(([path,graph],index)=>({id:path,kind:'chapter',label:path+'\n'+graph.entries.map(e=>e.kind==='start'?'START':e.source+'.'+e.tag).join(' 或 '),line:1,index,targetPath:path}));
  const edges=Object.entries(graphs).flatMap(([path,graph])=>graph.entries.filter(e=>e.kind==='continue').map((e,index)=>({id:e.source+'-'+path+'-'+index,source:e.source+'.flow',target:path,label:e.tag}))).filter(e=>graphs[e.source]);
  return {nodes,edges,entries:[]};
}
/** Match content references using the same project-root precedence as the compiler. */
export function graphWithFiles(graph:Graph,paths:string[]):Graph {
  return {...graph,nodes:graph.nodes.map(node=>{
    if(node.targetPath||node.kind!=='jump')return node;
    const target=[node.label,...['.txt','.chos','.gmf','.flow','.rule'].map(suffix=>node.label+suffix)].find(path=>paths.includes(path));
    return target?{...node,targetPath:target}:node;
  })};
}
export function connectSource(source:string,graph:Graph,from:string,to:string){
  const a=graph.nodes.find(n=>n.id===from),b=graph.nodes.find(n=>n.id===to);
  if(!a||!b||a.kind!=='jump'||b.kind!=='jump'||a.start==null||a.end==null||b.start==null||b.end==null||a.id===b.id)throw new Error('跳转连线需要连接两个内容节点；其他结构可在属性面板编辑。');
  const original=source.slice(b.start,b.end);const found=original.match(/<([\w+-]+)/);const alias=found?.[1]||'node_'+b.start;
  const edits:{start:number;end:number;text:string}[]=[];
  if(!found){const match=original.match(/^->[^\n{;]+/);if(!match)throw new Error('无法为目标添加别名');edits.push({start:b.start,end:b.start+match[0].length,text:match[0].trimEnd()+' <'+alias});}
  const snippet=source.slice(a.start,a.end);const replaced=snippet.replace(/^->\s*[^\n{;<]+/,'->'+alias+' ');
  edits.push({start:a.start,end:a.end,text:replaced});
  for(const edit of edits.sort((x,y)=>y.start-x.start))source=source.slice(0,edit.start)+edit.text+source.slice(edit.end);
  return source;
}
