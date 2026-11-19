import pageCopy from '../data/pageCopy.json';
import {useEffect,useRef,useState,type ReactNode} from 'react';
import type {Character,Room} from '../types';
import {api} from '../types';
import {storyApi} from './api';
import type {Project,StoryRun,Chapter} from './types';
import {useStoryRun} from './StoryProvider';
import {ChaptersPage} from './ChaptersPage';
import {PlayerPage} from './PlayerPage';
import {StoryBattleDialog} from './StoryBattleDialog';
import {StoryEditorPage} from './StoryEditorPage';
import './story.css';
export function StoryApp({page,session,catalog,room,onNavigate,onRoom,onDirty,renderCharacter}:{page:string;session:string;catalog:Character[];room:Room|null;onNavigate:(p:string)=>void;onRoom:(r:Room)=>void;onDirty:(dirty:boolean)=>void;renderCharacter:(c:Character)=>ReactNode}) {
  const story=useStoryRun(session);const [projects,setProjects]=useState<Project[]>([]),[defaultPath,setDefaultPath]=useState('');const [editorVisited,setEditorVisited]=useState(false);
  const current=useRef(story);current.current=story;
  const reload=async()=>{const result=await storyApi<{projects:Project[];default_workspace:string}>('/projects');setProjects(result.projects);setDefaultPath(result.default_workspace);};
  useEffect(()=>{if(session&&(page==='story'||page==='story/editor'))void reload().catch(e=>story.setError(e.message));if(page==='story/editor')setEditorVisited(true);},[session,page,story.run?.status]);
  const start=async(project:Project,chapter:Chapter,entry:string)=>{await story.start(project.id,chapter.path,entry);onNavigate('story/play');};
  const enterBattle=async()=>{let run=current.current.run;if(!run)return;if(run.status==='battle_ready')run=await story.command('startBattle') as StoryRun;if(run?.room){await story.restore(run.id);onRoom(await api<Room>('/rooms/'+run.room));}};
  const next=async(chapter:Chapter,entry:string)=>{if(!story.run)return;await story.start(story.run.project,chapter.path,entry);onNavigate('story/play');};
  return <>{story.error&&page.startsWith('story')&&!(page==='story/play'&&story.run)&&<div className="story-error" role="alert">{story.error}</div>}{page==='story'&&<ChaptersPage projects={projects} onStart={(p,c,e)=>void start(p,c,e).catch(()=>{})} onEditor={()=>{onNavigate('story/editor');}} onResume={()=>onNavigate('story/play')} hasRun={!!story.run}/>}<div hidden={page!=='story/editor'}>{session&&(editorVisited||page==='story/editor')&&<StoryEditorPage defaultPath={defaultPath} onBack={()=>onNavigate('story')} onPreview={run=>{story.accept(run);onNavigate('story/play');}} onDirty={onDirty}/>}</div>{story.run&&<><PlayerPage run={story.run} visible={page==='story/play'} inBattle={page==='battle'&&room?.story?.run===story.run.id} busy={story.busy} command={story.command} onBack={()=>onNavigate('story')} onContinue={(c,e)=>void next(c,e).catch(()=>{})} onBattle={()=>void enterBattle().catch(e=>story.setError(e.message))} onError={story.setError} error={story.error} encounter={<StoryBattleDialog run={story.run} catalog={catalog} busy={story.busy} onStart={()=>void enterBattle().catch(e=>story.setError(e.message))} renderCharacter={renderCharacter}/>}/>{page==='battle'&&room?.story?.run===story.run.id&&<div className="story-battle-banner"><span>剧情战斗 · 第 {room.story.wave+1}/{room.story.waves} 波</span><button className="button" onClick={()=>void story.command('save',{slot:'quick'}).catch(()=>{})}>保存战斗</button><button className="button" onClick={()=>onNavigate('story/play')}>{room.story.finished?'返回剧情':'剧情与存档'}</button></div>}</>}{page==='story/play'&&!story.run&&<div className="page"><h1>{pageCopy.storyChapters.emptyTitle}</h1><button className="button primary" onClick={()=>onNavigate('story')}>选择章节</button></div>}</>;
}
