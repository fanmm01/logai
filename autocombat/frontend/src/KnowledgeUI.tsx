import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { Character } from './types';
import { buildDefinitions, mechanicsSections, overview, splitTerms, termsFor, type Definition } from './knowledge';
import { EncyclopediaProse } from './EncyclopediaProse';
import './knowledge.css';

type Knowledge = { definitions: Definition[]; serial?: string; openCharacter: (c: Character) => void; openTerm: (id: string) => void };
const Context = createContext<Knowledge | null>(null);
export function KnowledgeProvider({ catalog, serial, openCharacter, openTerm, children }: Omit<Knowledge,'definitions'> & {catalog:Character[]; children:ReactNode}) {
  const definitions=useMemo(()=>buildDefinitions(catalog),[catalog]);
  return <Context.Provider value={{definitions,serial,openCharacter,openTerm}}>{children}</Context.Provider>;
}

export function HoverPreview({ children, content, onOpen, label, kind='term' }: {children:ReactNode;content:ReactNode;onOpen:()=>void;label:string;kind?:'term'|'character'}) {
  const anchor=useRef<HTMLSpanElement>(null), popup=useRef<HTMLDivElement>(null);
  const timer=useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [visible,setVisible]=useState(false), [position,setPosition]=useState({left:0,top:0});
  const clear=()=>{clearTimeout(timer.current);};
  const close=()=>{clear();setVisible(false);};
  const enter=()=>{clear();if(!visible) timer.current=setTimeout(()=>setVisible(true),1000);};
  const leave=()=>{clear();timer.current=setTimeout(()=>setVisible(false),180);};
  useEffect(()=>()=>clearTimeout(timer.current),[]);
  useEffect(()=>{
    if(!visible) return;
    const update=()=>{
      const a=anchor.current?.getBoundingClientRect(), p=popup.current?.getBoundingClientRect();
      if(!a||!p)return;
      let left=a.right+10,top=a.top;
      if(left+p.width>innerWidth-12)left=a.left-p.width-10;
      if(left<12)left=Math.min(Math.max(12,a.left),innerWidth-p.width-12);
      if(top+p.height>innerHeight-12)top=Math.max(12,innerHeight-p.height-12);
      setPosition({left,top});
    };
    update(); const observer=new ResizeObserver(update); if(popup.current)observer.observe(popup.current);
    const escape=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.stopImmediatePropagation();close();}};
    const away=(e:PointerEvent)=>{if(!anchor.current?.contains(e.target as Node)&&!popup.current?.contains(e.target as Node))close();};
    window.addEventListener('keydown',escape,true);window.addEventListener('pointerdown',away,true);
    const scroll=(e:Event)=>{if(!popup.current?.contains(e.target as Node))close();};
    window.addEventListener('scroll',scroll,true);window.addEventListener('resize',close);
    return()=>{observer.disconnect();window.removeEventListener('keydown',escape,true);window.removeEventListener('pointerdown',away,true);window.removeEventListener('scroll',scroll,true);window.removeEventListener('resize',close);};
  },[visible]);
  return <span ref={anchor} className={'preview-anchor '+kind+'-anchor'} onMouseEnter={enter} onMouseLeave={leave} onClickCapture={e=>{if(anchor.current?.contains(e.target as Node))close();}}
    onFocus={enter} onBlur={e=>{if(!popup.current?.contains(e.relatedTarget as Node))leave();}}>
    {children}
    {visible&&createPortal(<div ref={popup} className={'knowledge-popover '+kind+'-preview'} style={position}
      role="dialog" aria-label={label} onMouseEnter={clear} onMouseLeave={leave} onFocus={clear}
      onBlur={e=>{if(!popup.current?.contains(e.relatedTarget as Node)&&!anchor.current?.contains(e.relatedTarget as Node))leave();}}>
      <button className="preview-body" onClick={()=>{close();onOpen();}}>{content}<span className="preview-hint">点击查看{kind==='character'?'角色详情':'完整定义'} →</span></button>
    </div>,document.body)}
  </span>;
}

export function CharacterPreview({ character:c,children }: {character:Character;children:ReactNode}) {
  const knowledge=useContext(Context);
  if(!knowledge)return <>{children}</>;
  return <HoverPreview kind="character" label={c.name+'概览'} onOpen={()=>knowledge.openCharacter(c)} content={<>
    <strong>{c.name} <small>{c.serial}</small></strong>{overview(c).map((line,i)=><span className="preview-paragraph" key={i}>{line}</span>)}
  </>}>{children}</HoverPreview>;
}
export function CharacterLink({ character:c }: {character:Character}) {
  const knowledge=useContext(Context);
  return <CharacterPreview character={c}><a className="knowledge-link" href={'?character='+encodeURIComponent(c.serial)} onClick={e=>{e.preventDefault();knowledge?.openCharacter(c);}}>{c.name}</a></CharacterPreview>;
}
export function CardInfo({ character:c }: {character:Character}) {
  const knowledge=useContext(Context);
  return <button className="card-info" aria-label={'查看'+c.name+'详情'} onClick={()=>knowledge?.openCharacter(c)}>i</button>;
}
export function TermText({text}:{text:string}) {
  const knowledge=useContext(Context);
  const terms=useMemo(()=>knowledge?termsFor(knowledge.definitions,knowledge.serial):[],[knowledge?.definitions,knowledge?.serial]);
  if(!knowledge?.serial)return <>{text}</>;
  return <>{splitTerms(text,terms).map((part,i)=>typeof part==='string'?part:<HoverPreview key={i} label={part.name+'释义'} onOpen={()=>knowledge.openTerm(part.id)}
    content={<><strong>{part.name}</strong><span className="preview-paragraph">{part.text.slice(0,260)}{part.text.length>260?'…':''}</span>{part.sections.slice(0,1).map(id=><span className="preview-paragraph" key={id}>相关规则：{mechanicsSections.find(s=>s.id===id)?.content.split('\n')[0].replace(/^- /,'')}</span>)}</>}>
    <a className="knowledge-link" href={'?character='+knowledge.serial+'&term='+encodeURIComponent(part.id)} onClick={e=>{e.preventDefault();e.stopPropagation();knowledge.openTerm(part.id);}}>{part.name}</a>
  </HoverPreview>)}</>;
}

export function DefinitionPanel({id,onClose,onOpen}:{id:string;onClose:()=>void;onOpen:(id:string)=>void}) {
  const knowledge=useContext(Context);
  const definition=knowledge?.definitions.find(d=>d.id===id);
  const [stack,setStack]=useState<string[]>([]);
  const [section,setSection]=useState<string | null>(null);
  const previous=useRef(id),goingBack=useRef(false);
  useEffect(()=>{
    if(previous.current!==id){const prior=previous.current;if(!goingBack.current)setStack(old=>[...old,prior]);previous.current=id;goingBack.current=false;}
    setSection(null);
  },[id]);
  if(!definition)return <aside className="definition-panel"><button onClick={onClose}>关闭释义</button><p>此定义不存在或已更新。</p></aside>;
  const related=termsFor(knowledge!.definitions,knowledge?.serial).filter(d=>d.id!==id&&definition.text.includes(d.name)).slice(0,10);
  return <aside className="definition-panel" aria-label="名词定义"><div className="definition-toolbar">
    <button className="text-button" disabled={!stack.length&&!section} onClick={()=>{if(section)setSection(null);else{const next=[...stack];goingBack.current=true;onOpen(next.pop()!);setStack(next);}}}>← 返回</button>
    <button className="text-button" onClick={onClose}>关闭释义 ×</button></div>
    <h3>{section?mechanicsSections.find(s=>s.id===section)?.title:definition.name}</h3>
    <Context.Provider value={{...knowledge!,serial:undefined}}><EncyclopediaProse content={section?mechanicsSections.find(s=>s.id===section)!.content:definition.text}/></Context.Provider>
    {!section&&<><h4>相关机制</h4>{definition.sections.length?definition.sections.map(key=><button className="definition-related" key={key} onClick={()=>setSection(key)}>{mechanicsSections.find(s=>s.id===key)?.title}</button>):<p className="muted">此名词适用角色条目中的专属说明。</p>}
      {!!related.length&&<><h4>相关名词</h4>{related.map(d=><button className="definition-related" key={d.id} onClick={()=>onOpen(d.id)}>{d.name}</button>)}</>}
    </>}
  </aside>;
}

export function HelpPage({tab,catalog,navigate}:{tab:string;catalog:Character[];navigate:(page:string)=>void}) {
  return <div className="page help-page"><h1>帮助</h1><div className="tabs"><button className={tab==='mechanics'?'active':''} onClick={()=>navigate('help/mechanics')}>机制说明</button><button className={tab==='characters'?'active':''} onClick={()=>navigate('help/characters')}>角色卡帮助</button></div>
    {tab==='characters'?<div className="help-character-list">{[...catalog].sort((a,b)=>a.serial.localeCompare(b.serial,undefined,{numeric:true})).map(c=><CharacterLink key={c.serial} character={c}/>)}</div>:
    <article className="panel mechanics-article"><h2>游戏总机制</h2><p>角色条目明确写出的例外优先。</p><nav aria-label="机制目录">{mechanicsSections.map(s=><a key={s.id} href={'#'+s.id}>{s.title}</a>)}</nav>
      {mechanicsSections.map(s=><section key={s.id} id={s.id}><h3>{s.title}</h3><EncyclopediaProse content={s.content}/></section>)}
    </article>}
  </div>;
}
