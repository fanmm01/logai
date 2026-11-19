import pageCopy from '../data/pageCopy.json';
import {useEffect,useMemo,useRef,useState} from 'react';
import {FolderOpen,Save,Play,Check,FileText,GitBranch,Plus,ArrowLeft,Upload,Trash2,PanelLeft,X} from 'lucide-react';
import {storyApi} from './api';
import type {Workspace,SourceFile,Graph,StoryRun} from './types';
import {SourceEditor} from './SourceEditor';
import {FlowEditor} from './FlowEditor';
import {projectGraph,graphWithFiles} from './graph';
import {referenceForAsset} from './assets';
import './editor.css';

export function StoryEditorPage({defaultPath,onBack,onPreview,onDirty}:{defaultPath:string;onBack:()=>void;onPreview:(run:StoryRun)=>void;onDirty:(dirty:boolean)=>void}) {
  const [path,setPath]=useState(defaultPath),[workspace,setWorkspace]=useState<Workspace|null>(null),[tree,setTree]=useState<{path:string;text:boolean;size:number}[]>([]),[selected,setSelected]=useState('');
  const [files,setFiles]=useState<Record<string,SourceFile>>({}),[drafts,setDrafts]=useState<Record<string,string>>({}),[graphs,setGraphs]=useState<Record<string,Graph>>({}),[diagnostics,setDiagnostics]=useState<any[]>([]),[error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false),[mode,setMode]=useState(()=>window.matchMedia('(max-width: 900px)').matches?'source':'split'),[line,setLine]=useState(1),[newName,setNewName]=useState('');
  const [filesOpen,setFilesOpen]=useState(()=>!window.matchMedia('(max-width: 900px)').matches),[graphFile,setGraphFile]=useState('');
  useEffect(()=>{const media=window.matchMedia('(max-width: 900px)');const resize=()=>{setFilesOpen(!media.matches);if(media.matches)setMode('source');};media.addEventListener('change',resize);return()=>media.removeEventListener('change',resize);},[]);
  const [insertion,setInsertion]=useState<{id:number;text:string}|undefined>();
  const [positions,setPositions]=useState<Record<string,Record<string,{x:number;y:number}>>>({});
  const dirty=Object.keys(drafts).length>0;useEffect(()=>{onDirty(dirty);return()=>onDirty(false);},[dirty]);
  useEffect(()=>{if(!path&&defaultPath)setPath(defaultPath);},[defaultPath]);
  useEffect(()=>{if(!dirty)return;const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[dirty]);
  const active=useRef({workspace,files,drafts,selected});active.current={workspace,files,drafts,selected};
  const work=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');setMessage('');try{await fn();}catch(e){setError((e as Error).message);if((e as any).diagnostics)setDiagnostics((e as any).diagnostics);}finally{setBusy(false);}};
  const reloadTree=async(w:Workspace)=>{const result=await storyApi<{files:typeof tree}>('/workspaces/'+w.id+'/tree');setTree(result.files);return result.files;};
  const selectFile=async(name:string,w=workspace)=>{if(!w)return;setInsertion(undefined);setSelected(name);setLine(1);if(name.endsWith('.flow'))setGraphFile(name);if(window.matchMedia('(max-width: 900px)').matches)setFilesOpen(false);if(!active.current.files[name]){const file=await storyApi<SourceFile>('/workspaces/'+w.id+'/files?path='+encodeURIComponent(name));setFiles(old=>({...old,[name]:file}));}};
  const compile=async(w=workspace,overlays=drafts)=>{if(!w)return;try{const result=await storyApi<{graphs:Record<string,Graph>;diagnostics:any[]}>('/workspaces/'+w.id+'/compile',{drafts:overlays});setGraphs(result.graphs);setDiagnostics(result.diagnostics);}catch(e){setDiagnostics((e as any).diagnostics||[{message:(e as Error).message}]);}};
  const [workspaces,setWorkspaces]=useState<Workspace[]>([]),[storyName,setStoryName]=useState(''),[creating,setCreating]=useState(false);
  const refreshWorkspaces=async()=>{const result=await storyApi<{workspaces:Workspace[]}>('/workspaces');setWorkspaces(result.workspaces);};
  useEffect(()=>{const refresh=()=>void refreshWorkspaces().catch(e=>setError(e.message));refresh();window.addEventListener('focus',refresh);return()=>window.removeEventListener('focus',refresh);},[]);
  const loadWorkspace=async(w:Workspace)=>{
    const entries=(await storyApi<{files:typeof tree}>('/workspaces/'+w.id+'/tree')).files;
    const initial=entries.find(x=>x.path==='XU.flow')||entries.find(x=>x.path.endsWith('.flow'));
    const file=initial?await storyApi<SourceFile>('/workspaces/'+w.id+'/files?path='+encodeURIComponent(initial.path)):null;
    setWorkspace(w);setPath(w.path);setTree(entries);setFiles(file?{[file.path]:file}:{});setDrafts({});setSelected(file?.path||'');setGraphFile(file?.path||'');setGraphs({});setDiagnostics([]);setPositions({});setInsertion(undefined);setLine(1);setNewName('');
    await compile(w,{});
    try{const layout=await storyApi<SourceFile>('/workspaces/'+w.id+'/files?path=.story-editor.json');setPositions(JSON.parse(layout.content));setFiles(old=>({...old,['.story-editor.json']:layout}));}catch{setPositions({});}
    await refreshWorkspaces();
  };
  const canSwitch=()=>!dirty||confirm('当前故事有未保存草稿，切换将丢弃这些修改。继续？');
  const open=(target=path)=>{if(!canSwitch())return;void work(async()=>{await loadWorkspace(await storyApi<Workspace>('/workspaces/open',{path:target}));});};
  const createWorkspace=()=>{if(!storyName.trim()||!canSwitch())return;void work(async()=>{const w=await storyApi<Workspace>('/workspaces/create',{name:storyName.trim()});await loadWorkspace(w);setStoryName('');setCreating(false);setMessage('新故事已创建');});};
  useEffect(()=>{if(!workspace)return;const timer=setTimeout(()=>void compile(workspace,drafts),450);return()=>clearTimeout(timer);},[workspace,drafts]);
  const update=(name:string,value:string)=>setDrafts(old=>{const result={...old};if(value===files[name]?.content)delete result[name];else result[name]=value;return result;});
  const saveAll=()=>work(async()=>{const state=active.current;if(!state.workspace)return;const changes=Object.entries(state.drafts).map(([name,content])=>({path:name,content,baseHash:state.files[name]?.hash??null}));if(!changes.length)return;const result=await storyApi<{files:SourceFile[]}>('/workspaces/'+state.workspace.id+'/files',{changes});setFiles(old=>({...old,...Object.fromEntries(result.files.map(f=>[f.path,f]))}));setDrafts({});await reloadTree(state.workspace);setMessage('已保存到工作目录');});
  const content=drafts[selected]??files[selected]?.content??'';
  const create=()=>{if(!newName.trim()||!workspace)return;const name=newName.trim();setInsertion(undefined);setFiles(old=>({...old,[name]:{path:name,content:'',hash:null}}));setDrafts(old=>({...old,[name]:name.endsWith('.flow')?'@START:\n->END\n':''}));setTree(old=>[...old,{path:name,text:true,size:0}]);setSelected(name);setNewName('');};
  const rename=()=>work(async()=>{if(!workspace||!selected||!newName.trim())return;if(dirty)throw new Error('请先保存草稿，再重命名文件');const file=files[selected];await storyApi('/workspaces/'+workspace.id+'/files',{changes:[{op:'rename',path:selected,to:newName.trim(),baseHash:file.hash}]});setFiles({});await reloadTree(workspace);setSelected('');setNewName('');await compile(workspace,{});setMessage('文件与引用已更新');});
  const remove=()=>work(async()=>{if(!workspace||!selected)return;if(!confirm('删除 '+selected+'？'))return;await storyApi('/workspaces/'+workspace.id+'/files',{changes:[{op:'delete',path:selected,baseHash:files[selected].hash}]});setSelected('');await reloadTree(workspace);});
  const publish=()=>work(async()=>{if(!workspace)return;if(dirty)throw new Error('请先保存所有草稿');await storyApi('/workspaces/'+workspace.id+'/publish',{});setMessage('已发布新的不可变版本；旧运行继续使用原版本');});
  const preview=()=>work(async()=>{if(!workspace)return;const run=await storyApi<StoryRun>('/workspaces/'+workspace.id+'/preview',{drafts,flow:selected.endsWith('.flow')?selected:undefined});onPreview(run);});
  const positionChange=(p:Record<string,{x:number;y:number}>)=>{const next={...positions,[selected==='__overview'?'__overview':graphFile]:p};setPositions(next);update('.story-editor.json',JSON.stringify(next,null,2));};
  const graph = useMemo(()=>graphs[graphFile]?graphWithFiles(graphs[graphFile],tree.filter(file=>file.text).map(file=>file.path)):null,[graphs,graphFile,tree]);
  const overview = useMemo(()=>projectGraph(graphs),[graphs]);
  const graphContent=drafts[graphFile]??files[graphFile]?.content??'';
  const openGraphFile=(file:string,doubleClick:boolean)=>{
    if(selected!=='__overview'&&mode!=='split'&&!doubleClick)return;
    if(doubleClick&&mode==='graph')setMode('source');
    void selectFile(file).catch(e=>setError(e.message));
  };
  const focusFlow=(targetLine:number)=>{void selectFile(graphFile).then(()=>setLine(targetLine)).catch(e=>setError(e.message));};
  return <div className={'page story-editor'+(workspace?' workspace-open':'')}>
    <div className="page-intro"><div><div className="eyebrow">{pageCopy.storyEditor.eyebrow}</div><h1>故事创作室</h1><p>{pageCopy.storyEditor.description}</p></div><button className="button subtle" onClick={onBack}><ArrowLeft size={16}/>章节列表</button></div>
    <div className="story-workspace-picker">
      <label>故事工作区<select aria-label="切换故事工作区" disabled={busy} value={workspace?.id||''} onChange={e=>{const w=workspaces.find(x=>x.id===e.target.value);if(w)open(w.path);}}><option value="" disabled>选择故事工作区</option>{workspaces.map(w=><option key={w.id} value={w.id}>{w.name} · {w.path.split(/[\\/]/).pop()}</option>)}</select></label>
      <button className="button" disabled={busy} onClick={()=>setCreating(x=>!x)}><Plus size={16}/>新建故事</button>
      <button className="button subtle" disabled={busy} onClick={()=>void work(refreshWorkspaces)}>刷新列表</button>
    </div>
    {creating&&<form className="story-workspace-create" onSubmit={e=>{e.preventDefault();createWorkspace();}}><input aria-label="故事名称" placeholder="输入新故事名称" maxLength={100} value={storyName} onChange={e=>setStoryName(e.target.value)} autoFocus/><button className="button primary" disabled={busy||!storyName.trim()} type="submit">创建并打开</button><button className="button subtle" type="button" disabled={busy} onClick={()=>setCreating(false)}>取消</button></form>}
    <div className="story-workspace-bar"><FolderOpen size={19}/><input aria-label="工作区目录" value={path} onChange={e=>setPath(e.target.value)} placeholder="服务端工作文件夹路径"/><button className="button" disabled={busy||!path} onClick={()=>open()}>打开目录</button></div>
    {error&&<div className="story-error" role="alert">{error}</div>}{message&&<div className="story-success" role="status"><Check size={16}/>{message}</div>}
    {workspace&&<>
      <div className="story-editor-toolbar">
        <button className="button story-files-toggle" aria-expanded={filesOpen} aria-controls="story-files" onClick={()=>setFilesOpen(x=>!x)}><PanelLeft size={16}/>文件</button>
        <strong title={workspace.name}>{workspace.name}{dirty&&<span> · 未保存</span>}</strong>
        <div className="segmented">{[['source','源码'],['split','并排'],['graph','流程图']].map(([id,label])=><button key={id} className={mode===id?'active':''} onClick={()=>setMode(id)}>{label}</button>)}</div>
        <div className="story-editor-actions"><button className="button" disabled={busy||!dirty} onClick={saveAll}><Save size={15}/>全部保存</button><button className="button" disabled={busy} onClick={preview}><Play size={15}/>预览草稿</button><button className="button primary" disabled={busy||dirty||diagnostics.some(x=>x.severity==='error')} onClick={publish}><Upload size={15}/>发布</button></div>
        <button className="button subtle story-editor-back" aria-label="章节列表" onClick={onBack}><ArrowLeft size={16}/></button>
      </div>
      <div className={'story-editor-layout'+(filesOpen?' files-open':'')}>
        {filesOpen&&<button className="story-files-scrim" aria-label="收起文件列表" onClick={()=>setFilesOpen(false)}/>}
        <aside id="story-files" className="story-file-tree" inert={!filesOpen}>
          <div className="story-files-heading"><strong>项目文件</strong><button className="button subtle compact-button" aria-label="收起文件列表" onClick={()=>setFilesOpen(false)}><X size={16}/></button></div>
          <label>文件名<input aria-label="新文件名" value={newName} onChange={e=>setNewName(e.target.value)} placeholder="chapter4.flow"/></label>
          <div><button title="新建文件" className="button subtle compact-button" onClick={()=>{create();if(window.matchMedia('(max-width: 900px)').matches)setFilesOpen(false);}}><Plus size={15}/></button><button className="button subtle compact-button" onClick={rename} disabled={!newName||!selected}>重命名</button><button title="删除文件" className="button subtle compact-button" onClick={remove} disabled={!selected}><Trash2 size={14}/></button></div>
          <button className="button subtle" onClick={()=>{setSelected('__overview');if(window.matchMedia('(max-width: 900px)').matches)setFilesOpen(false);}}>总流程图</button>
          <nav aria-label="剧情文件">{tree.map(file=><button key={file.path} className={selected===file.path?'selected':''} title={file.path} onClick={()=>file.text?void selectFile(file.path).catch(e=>setError(e.message)):(()=>{try{if(!/\.(flow|txt|chos)$/.test(selected))throw new Error('请先选择剧情或流程文本');setMode('source');setFilesOpen(false);setInsertion({id:Date.now(),text:referenceForAsset(file.path,selected.endsWith('.flow'))});}catch(e){setError((e as Error).message);}})()}>{file.path.endsWith('.flow')?<GitBranch size={14}/>:<FileText size={14}/>}<span>{file.path}</span>{drafts[file.path]!==undefined&&<i/>}</button>)}</nav>
          <small>立绘最多五人，支持中英文逗号。<br/>__LOCK 放在选项编号之前。</small>
        </aside>
        <div className={'story-edit-panes mode-'+(selected==='__overview'?'graph':mode)}>
          {selected&&selected!=='__overview'&&mode!=='graph'&&<div className="story-source-pane"><div className="story-source-title">{selected}{drafts[selected]!==undefined&&<span>未保存</span>}</div><SourceEditor key={selected} value={content} onChange={text=>update(selected,text)} onSave={saveAll} line={line} insertion={insertion} diagnostics={diagnostics.filter(d=>d.path===selected)}/></div>}
          <div className="story-graph-pane" hidden={mode==='source'&&selected!=='__overview'}>
            {(mode!=='source'||selected==='__overview')&&(selected==='__overview'?<FlowEditor graph={overview} source="" readOnly onOpenFile={openGraphFile} onChange={()=>{}} onFocus={()=>{}} positions={positions.__overview||{}} onPositions={positionChange} onError={setError}/>:graph?<FlowEditor key={graphFile} graph={graph} source={graphContent} onOpenFile={openGraphFile} onChange={text=>update(graphFile,text)} onFocus={focusFlow} positions={positions[graphFile]||{}} onPositions={positionChange} onError={setError}/>:<div className="story-editor-empty">选择 .flow 文件查看并编辑流程图。</div>)}
          </div>
        </div>
      </div>
      <div className="story-diagnostics"><strong>{diagnostics.length?'需要检查':'语法检查通过'}</strong>{diagnostics.map((d,i)=><button key={i} onClick={()=>{if(d.path)void selectFile(d.path).then(()=>setLine(d.line||1));}}>{d.path}:{d.line} {d.message}</button>)}</div>
    </>}
  </div>;
}
