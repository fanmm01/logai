import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { Character } from './types';
import { buildDefinitions, definitionHelpAnchor, mechanicsSections, overview, specialistDefinitions, splitTerms, termsFor, type Definition } from './knowledge';
import { pageUrl } from './navigation';
import { EncyclopediaProse } from './EncyclopediaProse';
import './knowledge.css';
import { definitionIsNearby, interveningSkills, isOwnDefinition } from './definitionVisibility';

type Knowledge = { definitions: Definition[]; serial?: string; openCharacter: (c: Character) => void; openTerm: (id: string) => void; nearby?:ReadonlySet<Element>|null };
const Context = createContext<Knowledge | null>(null);
export function KnowledgeProvider({ catalog, serial, openCharacter, openTerm, children }: Omit<Knowledge,'definitions'> & {catalog:Character[]; children:ReactNode}) {
  const definitions=useMemo(()=>buildDefinitions(catalog),[catalog]);
  const [nearby,setNearby]=useState<ReadonlySet<Element>|null>(null);
  useEffect(()=>{
    if(!serial)return;
    let frame=0;
    const update=()=>{
      frame=0;
      const modal=document.querySelector('.encyclopedia-modal');if(!modal)return;
      const bounds=modal.getBoundingClientRect();
      const viewport={top:Math.max(0,bounds.top),bottom:Math.min(innerHeight,bounds.bottom),left:Math.max(0,bounds.left),right:Math.min(innerWidth,bounds.right)};
      const skills=[...modal.querySelectorAll('[data-skill-index]')];
      const sources=new Map<string,Element[]>();
      for(const node of modal.querySelectorAll('[data-definition-source]')){
        if(node.closest('.definition-panel'))continue;
        const key=node.getAttribute('data-definition-source')!;sources.set(key,[...(sources.get(key)||[]),node]);
      }
      const local=new Map(definitions.filter(d=>d.serial===serial).map(d=>[d.id,d]));
      const next=new Set<Element>();
      for(const reference of modal.querySelectorAll('[data-local-term]')){
        const definition=local.get(reference.getAttribute('data-local-term')!);
        const targets=definition?.sources?.flatMap(key=>sources.get(key)||[])||[];
        if(targets.some(target=>isOwnDefinition(reference,target)||!target.closest('details:not([open])')&&definitionIsNearby(reference.getBoundingClientRect(),target.getBoundingClientRect(),viewport,interveningSkills(reference,target,skills))))next.add(reference);
      }
      setNearby(old=>old&&old.size===next.size&&[...next].every(node=>old.has(node))?old:next);
    };
    const schedule=()=>{if(!frame)frame=requestAnimationFrame(update);};
    const modal=document.querySelector('.encyclopedia-modal');if(!modal)return;
    const resize=new ResizeObserver(schedule);resize.observe(modal);const profile=modal.querySelector('.character-profile');if(profile)resize.observe(profile);
    const mutation=new MutationObserver(schedule);mutation.observe(modal,{childList:true,subtree:true,attributes:true,attributeFilter:['open']});
    window.addEventListener('scroll',schedule,true);window.addEventListener('resize',schedule);schedule();
    return()=>{cancelAnimationFrame(frame);resize.disconnect();mutation.disconnect();window.removeEventListener('scroll',schedule,true);window.removeEventListener('resize',schedule);};
  },[serial,definitions]);
  return <Context.Provider value={{definitions,serial,openCharacter,openTerm,nearby}}>{children}</Context.Provider>;
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
  return <>{splitTerms(text,terms).map((part,i)=>typeof part==='string'?part:<DefinitionTerm key={i} definition={part}/>)}</>;
}
function DefinitionTerm({definition:part}:{definition:Definition}) {
  const knowledge=useContext(Context)!;
  const reference=useRef<HTMLSpanElement>(null);
  const local=part.serial===knowledge.serial;
  const plain=local&&(knowledge.nearby==null||!!reference.current&&knowledge.nearby.has(reference.current));
  const preview=part.text.replace(/^#{1,6}\s*|^[-*+]\s+/gm,'').replace(/\*\*|`/g,'');
  return <span ref={reference} data-local-term={local?part.id:undefined}>{plain?part.name:<HoverPreview label={part.name+'释义'} onOpen={()=>knowledge.openTerm(part.id)}
    content={<><strong>{part.name}</strong><span className="preview-paragraph">{preview.slice(0,260)}{preview.length>260?'…':''}</span>{part.sections.slice(0,1).map(id=><span className="preview-paragraph" key={id}>相关机制：{mechanicsSections.find(s=>s.id===id)?.title}</span>)}</>}>
    <a className="knowledge-link" href={'?character='+knowledge.serial+'&term='+encodeURIComponent(part.id)} onClick={e=>{e.preventDefault();e.stopPropagation();knowledge.openTerm(part.id);}}>{part.name}</a>
  </HoverPreview>}</span>;
}

export function DefinitionPanel({id,onClose,onOpen,onHelp}:{id:string;onClose:()=>void;onOpen:(id:string)=>void;onHelp:(anchor:string)=>void}) {
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
  const seen=new Set([definition.text]);
  const related=termsFor(knowledge!.definitions,knowledge?.serial).filter(d=>{
    if(d.serial||d.id===id||seen.has(d.text)||!definition.text.includes(d.name))return false;
    seen.add(d.text);return true;
  }).slice(0,10);
  const helpAnchor=definitionHelpAnchor(definition,section);
  return <aside className="definition-panel" aria-label="名词定义"><div className="definition-toolbar">
    <button className="text-button" disabled={!stack.length&&!section} onClick={()=>{if(section)setSection(null);else{const next=[...stack];goingBack.current=true;onOpen(next.pop()!);setStack(next);}}}>← 返回</button>
    <button className="text-button" onClick={onClose}>关闭释义 ×</button></div>
    <h3>{section?mechanicsSections.find(s=>s.id===section)?.title:definition.name}</h3>
    <Context.Provider value={{...knowledge!,serial:undefined}}><EncyclopediaProse content={section?mechanicsSections.find(s=>s.id===section)!.content:definition.text}/></Context.Provider>
    {!section&&<><h4>相关机制</h4>{definition.sections.length?definition.sections.map(key=><button className="definition-related" key={key} onClick={()=>setSection(key)}>{mechanicsSections.find(s=>s.id===key)?.title}</button>):<p className="muted">此名词适用角色条目中的专属说明。</p>}
      {!!related.length&&<><h4>相关名词</h4>{related.map(d=><button className="definition-related" key={d.id} onClick={()=>onOpen(d.id)}>{d.name}</button>)}</>}
    </>}
    <a className="button definition-help" href={pageUrl('help/mechanics')+'#'+encodeURIComponent(helpAnchor)} onClick={e=>{if(e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;e.preventDefault();onHelp(helpAnchor);}}>在帮助中查看 →</a>
  </aside>;
}

export function HelpPage({tab,catalog,navigate,anchor=''}:{tab:string;catalog:Character[];navigate:(page:string)=>void;anchor?:string}) {
  const [search,setSearch]=useState('');
  let target='';try{target=decodeURIComponent(anchor);}catch{target=anchor;}
  const query=search.trim().toLocaleLowerCase();
  const definitions=useMemo(()=>buildDefinitions(catalog),[catalog]);
  const linked=definitions.find(d=>d.serial&&d.id===target);
  useEffect(()=>setSearch(''),[anchor]);
  useEffect(()=>{
    if(tab!=='mechanics'||!target||query)return;
    const frame=requestAnimationFrame(()=>{
      const node=document.getElementById(target);if(!node)return;
      for(let parent=node.parentElement;parent;parent=parent.parentElement)if(parent instanceof HTMLDetailsElement)parent.open=true;
      node.scrollIntoView({block:'start'});
    });
    return()=>cancelAnimationFrame(frame);
  },[target,tab,catalog,query]);
  const matches=(text:string)=>text.toLocaleLowerCase().includes(query);
  const sections=mechanicsSections.map(s=>({...s,entries:s.entries.filter(entry=>matches(s.title+' '+entry.names.join(' ')+' '+entry.content))})).filter(s=>s.entries.length);
  const characters=[...catalog].sort((a,b)=>a.serial.localeCompare(b.serial,undefined,{numeric:true}));
  const local=characters.map(c=>{
    const terms=specialistDefinitions(definitions,c.serial);
    if(linked?.serial===c.serial&&!terms.some(d=>d.id===linked.id))terms.push(linked);
    return {character:c,terms:terms.filter(d=>matches(c.name+' '+c.serial+' '+d.name+' '+d.text))};
  }).filter(c=>c.terms.length);
  const count=sections.reduce((sum,s)=>sum+s.entries.length,0);
  return <div className="page help-page"><h1>帮助</h1><div className="tabs"><button className={tab==='mechanics'?'active':''} onClick={()=>navigate('help/mechanics')}>机制说明</button><button className={tab==='characters'?'active':''} onClick={()=>navigate('help/characters')}>角色卡帮助</button></div>
    {tab==='characters'?<div className="help-character-list">{characters.map(c=><CharacterLink key={c.serial} character={c}/>)}</div>:
    <article className="panel mechanics-article"><h2>游戏总机制</h2><p>按战斗流程查阅定义、结算步骤与例外。角色卡的释义与本页共用完整条目；专属数值和限制以当前角色百科为准。</p>
      <div className="mechanics-search"><label htmlFor="mechanics-search">搜索术语或机制</label><div><input id="mechanics-search" type="search" value={search} onChange={e=>setSearch(e.target.value)} placeholder="例如：闪避、濒死、月能、Y17"/>{search&&<button className="text-button" onClick={()=>setSearch('')}>清除搜索</button>}</div></div>
      <p className="muted mechanics-result" role="status">{query?'搜索结果：':'共 '}{count} 个通用条目，{local.reduce((sum,c)=>sum+c.terms.length,0)} 个角色专属条目</p>
      <nav aria-label="机制目录">{sections.map(s=><a key={s.id} href={'#'+s.id}>{s.title}</a>)}{!!local.length&&<a href="#mechanics-character-terms">角色专属机制</a>}</nav>
      {!count&&!local.length&&<p className="mechanics-empty">没有找到相关条目，请尝试其他术语、角色名或编号。</p>}
      {sections.map(s=><section key={s.id} id={s.id}><h3>{s.title}</h3>{s.entries.map(entry=><div className="mechanics-entry" key={entry.names[0]} id={'rule-'+entry.names[0]}><h4>{entry.names.join(' / ')}</h4><EncyclopediaProse content={entry.content}/></div>)}</section>)}
      {!!local.length&&<div className="mechanics-special" id="mechanics-character-terms"><h3>角色专属机制</h3><p>展开角色可查专属资源、状态与能力简称。点击角色名查看完整技能、当前数值和使用条件。</p>
        {local.map(({character:c,terms})=><details className="mechanics-character" key={c.serial} open={query?true:undefined}><summary>{c.name} · {c.serial}<span>{terms.length} 个条目</span></summary><p><CharacterLink character={c}/></p>{terms.map(d=><div className="mechanics-entry" key={d.id} id={d.id}><h4>{d.name}</h4><EncyclopediaProse content={d.text}/></div>)}</details>)}
      </div>}
    </article>}
  </div>;
}
