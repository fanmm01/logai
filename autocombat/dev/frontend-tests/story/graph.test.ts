import {describe,it,expect} from 'vitest';
import {layoutGraph,connectSource,projectGraph,graphWithFiles} from '../../../frontend/src/story/graph';
import type {Graph} from '../../../frontend/src/story/types';
const source='@START:\n->a\n->b\n';
const graph:Graph={nodes:[{id:'a',kind:'jump',label:'a',line:2,index:0,start:8,end:11},{id:'b',kind:'jump',label:'b',line:3,index:1,start:12,end:15}],edges:[{id:'a-b',source:'a',target:'b',label:''}],entries:[]};
describe('flow model',()=>{
  it('opens actual content files while preserving control nodes and explicit file paths',()=>{
    const withFiles=graphWithFiles({...graph,nodes:[...graph.nodes,{...graph.nodes[0],id:'control',kind:'gate'},{...graph.nodes[0],id:'explicit',targetPath:'chapter.flow'}]},['a.txt','a.chos','b']);
    expect(withFiles.nodes.map(n=>n.targetPath)).toEqual(['a.txt','b',undefined,'chapter.flow']);
    expect(graphWithFiles(graph,[]).nodes[0].targetPath).toBeUndefined();
    expect(graph.nodes[0].targetPath).toBeUndefined();
  });
  it('links chapter alternatives and keeps START independent',()=>{
    const overview=projectGraph({'A.flow':{...graph,entries:[{kind:'start'}]},'B.flow':{...graph,entries:[{kind:'start'},{kind:'continue',source:'A',tag:'CONTINUE-X'},{kind:'continue',source:'absent',tag:'CONTINUE-Y'}]}});
    expect(overview.nodes[1].label).toContain('START');expect(overview.nodes[1].targetPath).toBe('B.flow');expect(overview.edges).toHaveLength(1);expect(overview.edges[0].source).toBe('A.flow');
  });
  it('lays out a graph and preserves explicit positions',()=>{
    const result=layoutGraph(graph,{a:{x:3,y:4}});expect(result.nodes[0].position).toEqual({x:3,y:4});expect(result.edges).toHaveLength(1);expect(Number.isFinite(result.nodes[1].position.x)).toBe(true);
  });
  it('retargets a source and declares an alias without changing unrelated source',()=>{
    expect(connectSource(source,graph,'a','b')).toContain('->node_12');expect(connectSource(source,graph,'a','b')).toContain('->b <node_12');
  });
  it('preserves a declared target alias',()=>{
    const aliased='@START:\n->a\n->b <there\n';const g={...graph,nodes:[graph.nodes[0],{...graph.nodes[1],end:22}]};expect(connectSource(aliased,g,'a','b')).toContain('->there');
  });
  it('rejects missing/self/non-jump connections',()=>{
    expect(()=>connectSource(source,graph,'a','a')).toThrow();expect(()=>connectSource(source,graph,'a','none')).toThrow();expect(()=>connectSource(source,{...graph,nodes:[{...graph.nodes[0],kind:'gate'},graph.nodes[1]]},'a','b')).toThrow();
  });
});
