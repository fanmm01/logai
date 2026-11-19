import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, ArrowDownToLine, ArrowLeft, ArrowRight, BookOpen, CakeSlice, Check, ChevronDown, ChevronRight, CircleHelp, CirclePause, CirclePlay, Clock3, Compass, Copy, Diamond, Eye, Feather, Flame, Flower2, Gem, Heart, Home, Leaf, Link, LoaderCircle, MapPin, Moon, MoreHorizontal, MousePointer2, Plus, Radio, RotateCcw, Settings2, Shield, Snowflake, Sparkles, Star, StepForward, Sun, Sword, Swords, Users, Waves, Wifi, X, Zap, BarChart3, VolumeX } from 'lucide-react';
import { api, requestId, sortBySerial, type BattleEvent, type Character, type Effect, type Room, type Skill, type Snapshot, type Unit } from './types';
import './styles.css';
import pageCopy from './data/pageCopy.json';
import { characterStats } from './characterStats';
import { withEncyclopedia, type CatalogCharacter } from './encyclopedia';
import { EncyclopediaSkill } from './EncyclopediaSkill';
import { EncyclopediaProse } from './EncyclopediaProse';
import { CharacterPreview, CardInfo, DefinitionPanel, HelpPage, KnowledgeProvider, TermText } from './KnowledgeUI';
import { pageFromPath, pageUrl } from './navigation';
import './expansion.css';
import { CombatControls, BonusSelect } from './CombatControls';
import { AutoReactionControls, DataEditor, SimulationPanel, TournamentPanel } from './Expansion';
import { SettingsPage } from './SettingsPage';
import { StoryApp } from './story/StoryApp';
import { RuntimeSettings, readPreferences, savePreferences, type SettingsResponse, type SettingsValues, type Preferences } from './settings';

const accents = ['#db775a', '#8a78c5', '#5b9b91', '#c88c45', '#c56785', '#6694bc', '#9a80b1', '#cb8b9e', '#658878', '#c29b52', '#688ab6', '#847fc0'];
type CatalogResponse = { characters: CatalogCharacter[]; summon_templates?: Record<string, Effect>; item_templates?: Record<string, Effect> };
const hydrateCatalog = (data: CatalogResponse) => sortBySerial(data.characters.map(character => withEncyclopedia({
  ...character, summon_templates: data.summon_templates || {}, item_templates: data.item_templates || {},
})));
const styleColor = (color: string): CSSProperties => ({ '--character': color } as CSSProperties);
function Emblem({ name = 'star', size = 24, className = '' }: { name?: string; size?: number; className?: string }) {
  return <span aria-hidden="true" className={'library-emblem ' + className} style={{ width:size, height:size, maskImage:`url(/ui/icons/${name}.svg)`, WebkitMaskImage:`url(/ui/icons/${name}.svg)` }}/>;
}
function IconButton({ title, children, onClick, disabled = false, className = '' }: { title: string; children: ReactNode; onClick?: () => void; disabled?: boolean; className?: string }) { return <button className={'icon-button ' + className} title={title} aria-label={title} onClick={onClick} disabled={disabled}>{children}</button>; }
function Tag({ children, className = '' }: { children: ReactNode; className?: string }) { return <span className={'tag ' + className}>{children}</span>; }
function AiCheckbox({ checked, onChange, disabled = false, label, title }: { checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean; label: string; title?: string }) {
  return <label className="slot-control" title={title}><input type="checkbox" aria-label={label} checked={checked} disabled={disabled} onChange={e => onChange(e.target.checked)}/>AI</label>;
}
function Modal({ title, children, onClose, wide = false, className = '' }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean; className?: string }) { return <div className="modal-scrim" onClick={onClose}><section className={'modal ' + (wide ? 'wide ' : '') + className} role="dialog" aria-modal="true" aria-label={title} onClick={e => e.stopPropagation()}><header><h2>{title}</h2><IconButton title="关闭" onClick={onClose}><X size={19}/></IconButton></header>{children}</section></div>; }
function Sigil({ character, compact = false }: { character?: Character; compact?: boolean }) {
  const color = character?.color || '#8a78c5';
  return <div className={'sigil ' + (compact ? 'compact' : '')} style={styleColor(color)}>
    <svg viewBox="0 0 240 260" aria-hidden="true"><defs><linearGradient id={'fade-' + character?.serial} x1="0" y1="0" x2="1" y2="1"><stop stopColor={color} stopOpacity=".25"/><stop offset="1" stopColor={color} stopOpacity=".02"/></linearGradient></defs><circle cx="120" cy="126" r="85" fill={'url(#fade-' + character?.serial + ')'} stroke={color} strokeOpacity=".22"/><circle cx="120" cy="126" r="67" fill="none" stroke={color} strokeOpacity=".35" strokeDasharray="1 7"/><path d="M120 23L210 178H30Z M120 229L30 74H210Z" fill="none" stroke={color} strokeOpacity=".22"/><path d="M15 126H225M120 13V239" stroke={color} strokeOpacity=".12"/><path d="M120 48 Q167 85 198 126 Q164 168 120 204 Q78 167 42 126 Q77 82 120 48Z" fill="none" stroke={color} strokeOpacity=".25"/>{[[120,23],[210,178],[30,178],[30,74],[210,74],[120,229]].map(([cx,cy],i)=><circle key={i} cx={cx} cy={cy} r="3" fill={color} opacity=".65"/>)}<path d="M31 28L34 35L41 38L34 41L31 48L28 41L21 38L28 35Z M207 209L210 216L217 219L210 222L207 229L204 222L197 219L204 216Z" fill={color} opacity=".55"/></svg>
    <div className="sigil-icon"><Emblem name={character?.emblem} size={compact ? 35 : 56}/></div>
    {character && <img src={character.portrait} alt="" className="portrait" onError={e => { e.currentTarget.style.display = 'none'; }}/>} 
  </div>;
}
function CharacterCard({ character: c, selected = false, onClick, small = false }: { character: Character; selected?: boolean; onClick?: () => void; small?: boolean }) { return <div className="card-with-info"><CharacterPreview character={c}><button className={'character-card ' + (selected ? 'selected ' : '') + (small ? 'small' : '')} style={styleColor(c.color)} onClick={onClick} aria-label={c.name + (selected ? '，已选择' : '')}><div className="card-top"><span>{c.serial}</span><Diamond size={12}/></div><Sigil character={c}/><div className="card-name"><span className="role-traits">{c.traits.map(t=><span key={t.name} title={t.description}>{t.name}</span>)}</span><h3>{c.name}</h3><div className="card-bottom"><span>MAGICAL GIRL</span>{selected ? <Check size={16}/> : <ArrowUpRight/>}</div></div></button></CharacterPreview>{small&&<CardInfo character={c}/>}</div>; }
function ArrowUpRight() { return <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4 12L12 4M4 4H12V12" stroke="currentColor" strokeWidth="1.5"/></svg>; }
function Resource({ label, value, max, kind = 'hp' }: { label: string; value: number; max: number; kind?: string }) { return <div className={'resource ' + kind}><div><span>{label}</span><strong>{value}<small> / {max}</small></strong></div><i><b style={{ width: Math.max(0, Math.min(100, value / Math.max(1,max) * 100)) + '%' }}/></i></div>; }
function CharacterStats({ character }: { character: Character }) {
  const stats = characterStats(character.attrs, character.serial, character.str_attrs, character.spells);
  return <section className="profile-stats"><table>
    <caption>角色数值表</caption>
    <thead><tr><th scope="col">属性</th><th scope="col">变身前</th><th scope="col">变身后</th></tr></thead>
    <tbody>{stats.map(stat => <tr key={stat.label}><th scope="row"><TermText text={stat.label}/></th><td>{stat.before ?? '—'}</td><td>{stat.after ?? '—'}</td></tr>)}</tbody>
  </table><p>体力、魔力为满值；攻击次数按每轮主动作数计算。不含技能、被动与状态加成。</p></section>;
}
function coordPoint(coord: string) { const m = /^([A-Z])(\d+)$/.exec(coord); return m ? [m[1].charCodeAt(0)-65, Number(m[2])-1] : [-1,-1]; }
function App() {
  const [catalog, setCatalog] = useState<Character[]>([]);
  const [presets, setPresets] = useState<Effect[]>([]);
  const updateRoom = (r: Room) => setRoom(old => old?.code === r.code && r.revision >= old.revision ? r : old);
  const reloadCatalog = () => { api<CatalogResponse>('/catalog').then(x=>{setCatalog(hydrateCatalog(x));const valid=new Set(x.characters.map(c=>c.serial));setTeamA(old=>old.filter(s=>valid.has(s)));setTeamB(old=>old.filter(s=>valid.has(s)));}); api<{presets:Effect[]}>('/presets').then(x=>setPresets(x.presets)); };
  useEffect(()=>{ api<{presets:Effect[]}>('/presets').then(x=>setPresets(x.presets)).catch(()=>{}); },[]);
  const [page, setPage] = useState(() => pageFromPath(location.pathname));
  const [name, setName] = useState(readPreferences().name);
  const [defaults, setDefaults] = useState<SettingsValues | null>(null);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [session, setSession] = useState('');
  const [room, setRoom] = useState<Room | null>(null);
  const [history, setHistory] = useState<BattleEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [dialog, setDialog] = useState('');
  const [joinCode, setJoinCode] = useState(new URLSearchParams(location.search).get('room') || '');
  const [spectator, setSpectator] = useState(false);
  const [reduced, setReduced] = useState(readPreferences().reduced);
  const [teamA, setTeamA] = useState(['Y4','Y8']);
  const [teamB, setTeamB] = useState(['Y1','Y7']);
  const [editTeam, setEditTeam] = useState('Y');
  const [mode, setMode] = useState('local');
  const [aiSlots, setAiSlots] = useState<string[]>([]);
  const [mapSize, setMapSize] = useState('');
  const [filter, setFilter] = useState('');
  const [detailSerial, setDetailSerial] = useState(() => new URLSearchParams(location.search).get('character'));
  const detail = catalog.find(c => c.serial === detailSerial) || null;
  const [term, setTerm] = useState(() => new URLSearchParams(location.search).get('term'));
  const detailOrigin = useRef<string | null>(null);
  const detailScroll = useRef(0);
  const definitionOriginScroll = useRef(0);
  const pageScroll = useRef<Record<string,number>>({});
  const routeRef = useRef({page,settingsDirty,url:location.pathname+location.search+location.hash});
  routeRef.current={page,settingsDirty,url:location.pathname+location.search+location.hash};
  const navigationIndex=useRef<number>(window.history.state?.navigationIndex || 0);
  const undoPop=useRef(false);
  const commitUrl = (url: string, replace = false) => {
    window.history.replaceState({...window.history.state,navigationIndex:navigationIndex.current,scroll:window.scrollY},'',location.href);
    if(!replace)navigationIndex.current++;
    window.history[replace?'replaceState':'pushState']({navigationIndex:navigationIndex.current,scroll:window.scrollY},'',url);
  };
  const setDetail = (character: Character | null) => {
    if (character && !detailSerial) detailOrigin.current=location.pathname+location.search+location.hash;
    if (character && detail?.serial!==character.serial) detailScroll.current=0;
    const url=new URL(location.href);
    if(character) url.searchParams.set('character',character.serial); else url.searchParams.delete('character');
    url.searchParams.delete('term');
    if(!character&&detailOrigin.current) {
      window.history.back(); detailOrigin.current=null; return;
    }
    commitUrl(url.pathname+url.search+url.hash);
    setDetailSerial(character?.serial || null); setTerm(null);
  };
  const openTerm = (id: string) => {
    if(!term)definitionOriginScroll.current=document.querySelector('.encyclopedia-modal')?.scrollTop || 0;
    const url=new URL(location.href); url.searchParams.set('term',id);
    commitUrl(url.pathname+url.search+url.hash,true);setTerm(id);
  };
  const closeTerm = () => {
    const url=new URL(location.href);url.searchParams.delete('term');commitUrl(url.pathname+url.search+url.hash,true);setTerm(null);
    requestAnimationFrame(()=>document.querySelector('.encyclopedia-modal')?.scrollTo({top:definitionOriginScroll.current,behavior:'instant'}));
  };
  const [selected, setSelected] = useState('');
  const [bonus, setBonus] = useState('');
  const [timing, setTiming] = useState(0);
  const [failedReaction, setFailedReaction] = useState(true);
  const [skill, setSkill] = useState<Skill | null>(null);
  const [moveMode, setMoveMode] = useState(false);
  const [giveMode, setGiveMode] = useState(false);
  const [options, setOptions] = useState<{ cells: string[]; targets: string[] }>({cells:[],targets:[]});
  const [draft, setDraft] = useState<Effect | null>(null);
  const [replay, setReplay] = useState(-1);
  const [logTab, setLogTab] = useState('log');
  const [logFilter, setLogFilter] = useState('');
  const [toast, setToast] = useState('');
  const restored = useRef(false);
  const legacyInvite=useRef(/^\/ui\/?$/.test(location.pathname)&&new URLSearchParams(location.search).has('room'));
  const navigate = (next: string, replace = false, anchor = '') => {
    if(next===page&&!detailSerial)return;
    if (page !== next && page === 'settings' && settingsDirty && !confirm('放弃未保存的设置修改？')) return;
    pageScroll.current[page]=window.scrollY;
    const params=new URLSearchParams(location.search); params.delete('character');params.delete('term');
    commitUrl(pageUrl(next,params.size?'?'+params:'')+(anchor?'#'+encodeURIComponent(anchor):''),replace);
    setDetailSerial(null);setTerm(null);detailOrigin.current=null;
    setSettingsDirty(false);setPage(next);
    if(!anchor)requestAnimationFrame(()=>window.scrollTo(0,pageScroll.current[next]||0));
  };
  useEffect(()=>{
    const normalized=pageFromPath(location.pathname);
    window.history.replaceState({...window.history.state,navigationIndex:navigationIndex.current,scroll:window.scrollY},'',pageUrl(normalized,location.search)+location.hash);
    const pop=(event:PopStateEvent)=>{
      if(undoPop.current){undoPop.current=false;return;}
      const next=pageFromPath(location.pathname),current=routeRef.current;
      if(next!==current.page&&current.page==='settings'&&current.settingsDirty&&!confirm('放弃未保存的设置修改？')) {
        const delta=navigationIndex.current-(event.state?.navigationIndex ?? navigationIndex.current);
        if(delta){undoPop.current=true;window.history.go(delta);}else window.history.pushState({navigationIndex:navigationIndex.current},'',current.url);return;
      }
      navigationIndex.current=event.state?.navigationIndex ?? navigationIndex.current;
      setPage(next);if(next!==current.page)setSettingsDirty(false);
      const params=new URLSearchParams(location.search);setDetailSerial(params.get('character'));setTerm(params.get('term'));
      requestAnimationFrame(()=>window.scrollTo(0,event.state?.scroll||0));
    };
    window.addEventListener('popstate',pop);return()=>window.removeEventListener('popstate',pop);
  },[]);
  useEffect(()=>{
    if(!detail)return;
    const modal=document.querySelector('.encyclopedia-modal');if(modal)modal.scrollTop=detailScroll.current;
    const reveal=term&&innerWidth<=1100?requestAnimationFrame(()=>{
      const panel=document.querySelector('.definition-panel');
      if(modal&&panel)modal.scrollTo({top:modal.scrollTop+panel.getBoundingClientRect().top-modal.getBoundingClientRect().top-16,behavior:'instant'});
    }):undefined;
    const remember=()=>{if(modal)detailScroll.current=modal.scrollTop;};modal?.addEventListener('scroll',remember);
    const escape=(e:KeyboardEvent)=>{if(e.key==='Escape'){if(term)closeTerm();else setDetail(null);}};
    window.addEventListener('keydown',escape);
    return()=>{if(reveal)cancelAnimationFrame(reveal);modal?.removeEventListener('scroll',remember);window.removeEventListener('keydown',escape);};
  },[detail?.serial,term]);
  const updatePreferences = async (prefs: Preferences) => { const cleaned = {...prefs, name: prefs.name.trim()}; if (!cleaned.name) throw new Error('请填写你的称呼'); await api('/session', {name: cleaned.name}); savePreferences(cleaned); setName(cleaned.name); setReduced(cleaned.reduced); };
  const ch = (serial: string) => catalog.find(c=>c.serial===serial);
  const view = replay >= 0 ? history[replay]?.snapshot || room?.snapshot : room?.snapshot;
  const activeUnit = view?.units.find(u=>u.uid===view.turn?.uid);
  const selectedUnit = view?.units.find(u=>u.uid===selected) || activeUnit;
  const host = room?.host === session;
  const canAct = !!room && replay < 0 && view?.turn?.controller === session && view?.state.phase === 'active' && !view?.reaction;
  const isPrep = view?.state.phase === 'preparation';
  const notice = (message: string) => { setToast(message); setTimeout(()=>setToast(''),3000); };
  const act = async (fn: () => Promise<void>) => { setBusy(true); setError(''); try { await fn(); } catch(e) { setError((e as Error).message); } finally { setBusy(false); } };

  function acceptRoom(r: Room, foreground = true) {
    setRoom(old=>!old||r.code!==old.code||r.revision>=old.revision?r:old);
    if (r.history.length) setHistory(r.history);
    if(foreground) navigate('battle'); setDialog(''); setReplay(-1);
    if(!r.story)localStorage.setItem('combat-room',r.code);
    const url = new URL(location.href); url.searchParams.set('room',r.code); window.history.replaceState(window.history.state,'',url);
  }
  useEffect(()=>{
    Promise.all([api<CatalogResponse>('/catalog'),api<{id:string}>('/session',{name}),api<SettingsResponse>('/settings')]).then(([c,s,config])=>{setCatalog(hydrateCatalog(c));setSession(s.id);setDefaults(config.values);setMapSize(config.values.DEFAULT_MAP_SIZE);setTiming(config.values.DEFAULT_RESTIM_MODE);setFailedReaction(config.values.ALLOW_FAILED_REACTION);setConnected(true);}).catch(e=>setError(e.message));
  },[]);
  useEffect(()=>{
    if (!session || restored.current) return;
    restored.current = true;
    const code = new URLSearchParams(location.search).get('room') || localStorage.getItem('combat-room');
    if (code) { const foreground=location.pathname==='/ui/battle'||legacyInvite.current; api<Room>('/rooms/'+code).then(r=>acceptRoom(r,foreground)).catch(()=>{ setJoinCode(code); if(foreground)setDialog('join'); localStorage.removeItem('combat-room'); }); }
  },[session]);
  useEffect(()=>{
    document.documentElement.dataset.reduced = String(reduced);
    localStorage.setItem('combat-reduced',String(reduced));
  },[reduced]);
  useEffect(()=>{
    if (!room) return;
    const stream = new EventSource(`/api/ui/rooms/${room.code}/events?after=${room.revision}`);
    stream.onopen = ()=>setConnected(true);
    stream.onerror = ()=>setConnected(false);
    stream.onmessage = message=>{
      const data = JSON.parse(message.data);
      const event: BattleEvent = data.event;
      setHistory(old=>old.some(e=>e.id===event.id) ? old : [...old,event].slice(-Number(defaults?.ROOM_HISTORY_LIMIT)));
      setRoom(old=>old && event.id >= old.revision ? {...old,snapshot:event.snapshot,revision:event.id,running:data.running,speed:data.speed,members:data.members,controls:data.controls,tournament:data.tournament,story:data.story} : old);
    };
    stream.addEventListener('reset',()=>api<Room>('/rooms/'+room.code).then(r=>{setRoom(old=>!old||r.code!==old.code||r.revision>=old.revision?r:old);setHistory(r.history);setReplay(-1);}).catch(e=>setError(e.message)));
    return ()=>stream.close();
  },[room?.code]);
  useEffect(()=>{setSkill(null);setMoveMode(false);setGiveMode(false);},[view?.turn?.slot_uid,view?.state.phase]);
  useEffect(()=>{
    setOptions({cells:[],targets:[]});
    const command = moveMode ? 'move' : giveMode ? '.a give' : skill?.command;
    const actor = isPrep ? selectedUnit?.uid : activeUnit?.uid;
    if (!room || !command || replay>=0 || !actor) return;
    let cancelled=false;
    api<{cells:string[];targets:string[]}>(`/rooms/${room.code}/options`,{command,actor,revision:room.revision}).then(data=>{if(!cancelled)setOptions(data);}).catch(e=>{if(!cancelled)setError(e.message);});
    return ()=>{cancelled=true;};
  },[room?.code,room?.revision,moveMode,giveMode,skill?.command,isPrep?selectedUnit?.uid:activeUnit?.uid,replay]);
  useEffect(()=>{const handler=(e:KeyboardEvent)=>{if(e.key==='Escape'){setDraft(null);setSkill(null);setMoveMode(false);setGiveMode(false);setDialog('');}};window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler);},[]);

  const control = (data: Effect) => act(async()=>{if(!room)return;const r=await api<Room>(`/rooms/${room.code}/control`,data);setRoom(old=>!old||r.code!==old.code||r.revision>=old.revision?r:old);});
  const create = () => act(async()=>{
    await api('/session',{name});savePreferences({name,reduced});
    const slots=mode==='watch'?[...teamA.map(s=>'Y:'+s),...teamB.map(s=>'X:'+s)]:aiSlots;
    const r=await api<Room>('/rooms',{team_a:teamA,team_b:teamB,map_size:mapSize,local:mode!=='lan',ai_slots:slots,player_name:name,name:mode==='watch'?'星光演习 · AI 对决':mode==='lan'?'星光演习 · 好友房间':'星光演习 · 自由对战'});
    setHistory([]);acceptRoom(r);
  });
  const join = () => act(async()=>{await api('/session',{name});savePreferences({name,reduced});acceptRoom(await api<Room>(`/rooms/${joinCode.trim().toUpperCase()}/join`,{name,spectator}));});
  const prepare = (newMode:string) => {if (defaults) {setMapSize(defaults.DEFAULT_MAP_SIZE);setTiming(defaults.DEFAULT_RESTIM_MODE);setFailedReaction(defaults.ALLOW_FAILED_REACTION);}setMode(newMode);navigate('setup');setFilter('');};
  const nextTournamentGame = () => act(async()=>{
    if (!room?.tournament) return;
    const result = await api<{room: Room}>(`/tournaments/${room.tournament.id}/next`, {room:room.code});
    setHistory([]); acceptRoom(result.room);
  });
  const toggleTeam = (serial:string) => {const list=editTeam==='Y'?teamA:teamB;const setter=editTeam==='Y'?setTeamA:setTeamB;if(list.includes(serial))setter(list.filter(s=>s!==serial));else if(list.length<6)setter([...list,serial]);else notice('每队最多 6 名角色');};
  const preview = (command:string,target='',label?:string,actor?:string) => act(async()=>{
    if(!room)return;
    const data={command,target,revision:room.revision,actor:actor || activeUnit?.uid,bp_suffix:bonus,restim_mode:timing,allow_failed_reaction:failedReaction?1:0};
    await api(`/rooms/${room.code}/preview`,data);
    setDraft({...data,request_id:requestId(),label:label || command,targetName:view?.units.find(u=>u.uid===target)?.name || view?.graveyard.find(g=>g.uid===target)?.name || target || '由技能规则选择',cost:skill?.mp_cost});
  });
  const send = () => act(async()=>{if(!room || !draft)return;const r=await api<Room>(`/rooms/${room.code}/action`,{...draft});setRoom(old=>!old||r.code!==old.code||r.revision>=old.revision?r:old);setDraft(null);setSkill(null);setMoveMode(false);setGiveMode(false);});
  const react = (choice:string) => act(async()=>{if(!room)return;setRoom(await api<Room>(`/rooms/${room.code}/action`,{choice,bp_suffix:bonus,revision:room.revision,request_id:'reaction-'+room.revision+'-'+choice}));});
  const selectUnit = (u:Unit) => {setSelected(u.uid);if(!skill&&!giveMode)setLogTab('unit');if(skill&&options.targets.includes(u.uid))preview(skill.command,u.uid,skill.name);else if(giveMode&&options.targets.includes(u.uid))preview('.a give',u.uid,'给予蛋糕');};
  const selectCell = (coord:string) => {if(moveMode&&options.cells.includes(coord))preview('.a m '+coord,'','移动至 '+coord,isPrep?selectedUnit?.uid:activeUnit?.uid);};
  const copyLink = async()=>{if(['127.0.0.1','localhost'].includes(location.hostname)){setDialog('invite');return;}try{await navigator.clipboard.writeText(location.href);notice('邀请链接已复制');}catch{setDialog('invite');}};
  const leave = ()=>act(async()=>{if(room)await api(`/rooms/${room.code}/control`,{op:'leave'});setRoom(null);setHistory([]);navigate('home');setDialog('');localStorage.removeItem('combat-room');const u=new URL(location.href);u.search='';window.history.replaceState(window.history.state,'',u);});

  function TeamEditor({ sim = false }: {sim?:boolean}) {return <div className="team-editor"><div className="section-heading"><div><span className="eyebrow">{pageCopy.setup.formationEyebrow}</span><h2>{sim?'模拟阵容':pageCopy.setup.formationTitle}</h2></div><span className="muted">每队 1–6 人 · 支持镜像对决</span></div><div className="preset-bar"><label>预设小队<select aria-label="预设小队" value="" onChange={e=>{const p=presets[Number(e.target.value)];if(p)(editTeam==='Y'?setTeamA:setTeamB)(p.team);}}><option value="">选择预设，替换当前编队</option>{presets.map((p,i)=><option key={i} value={i} disabled={!p.valid}>{p.format} · {p.team.map((s:string)=>ch(s)?.name||s).join(' + ')}{!p.valid?'（角色已禁用或已删除）':''}</option>)}</select></label></div><div className="formation-teams">{[['Y',teamA],['X',teamB]].map(([team,raw])=>{const list=raw as string[];return <section key={team as string} className={'formation-team '+(editTeam===team?'editing':'')}><button className="team-title" onClick={()=>setEditTeam(team as string)}><span className={'team-dot '+(team==='X'?'orange':'')}/><strong>{team==='Y'?'星辉小队':'晨曦小队'}</strong><Tag>{list.length} / 6</Tag>{editTeam===team&&<span className="selecting">正在编队</span>}</button><div className="formation-slots">{list.map(serial=>{const c=ch(serial);return c&&<div className="formation-slot" key={serial} style={styleColor(c.color)}><span className="mini-avatar"><Emblem name={c.emblem} size={24}/></span><div><strong>{c.name}</strong><small>{c.role}</small></div><IconButton title={'移除'+c.name} onClick={()=>{(team==='Y'?setTeamA:setTeamB)(list.filter(s=>s!==serial));}}><X size={14}/></IconButton>{!sim&&mode!=='watch'&&<AiCheckbox checked={aiSlots.includes(team+':'+serial)} label={`${team} ? ${c.name} AI`} onChange={checked=>setAiSlots(old=>checked?[...old,team+':'+serial]:old.filter(x=>x!==team+':'+serial))}/>}</div>;})}<button className="empty-slot" onClick={()=>setEditTeam(team as string)}><Plus size={20}/><span>选择角色加入</span></button></div></section>;})}</div><div className="pool-heading"><div className="tabs"><button className={!filter?'active':''} onClick={()=>setFilter('')}>全部角色 <span>{catalog.length}</span></button><button className={filter==='支援'?'active':''} onClick={()=>setFilter('支援')}>治疗与支援</button><button className={filter==='召唤'?'active':''} onClick={()=>setFilter('召唤')}>召唤与策略</button></div><span className="muted">点击卡牌，加入{editTeam==='Y'?'星辉':'晨曦'}小队</span></div><div className="character-grid selection-grid">{catalog.filter(c=>!filter||c.role.includes(filter)||(filter==='支援'&&/治疗|增益|辅助/.test(c.role))).map(c=><CharacterCard key={c.serial} character={c} selected={(editTeam==='Y'?teamA:teamB).includes(c.serial)} onClick={()=>toggleTeam(c.serial)} small/>)}</div></div>;}

  function Board({ snapshot:s }: {snapshot:Snapshot}) {
    const w=s.map.width,h=s.map.height,cell=60,pad=34;
    const reps=s.units.filter(u=>!u.summon||u.representative===u.uid);
    return <div className={'board-viewport '+(moveMode?'move-mode':'')}><svg className="battle-board" viewBox={`0 0 ${w*cell+pad*2} ${h*cell+pad*2}`} role="img" aria-label={`${w}乘${h}战术棋盘`}>
      <defs><pattern id="grid" width={cell} height={cell} patternUnits="userSpaceOnUse" x={pad} y={pad}><rect width={cell} height={cell} fill="#fafafd"/><path d={`M ${cell} 0 H 0 V ${cell}`} fill="none" stroke="#e4e5ef" strokeWidth="1"/></pattern><radialGradient id="board-glow"><stop stopColor="#e7e1f6" stopOpacity=".6"/><stop offset="1" stopColor="#f6f5fa" stopOpacity="0"/></radialGradient></defs>
      <rect x={pad} y={pad} width={w*cell} height={h*cell} rx="4" fill="url(#grid)" stroke="#e1e2eb"/>
      <circle cx={pad+w*cell/2} cy={pad+h*cell/2} r={w*cell*.32} fill="url(#board-glow)" pointerEvents="none"/>
      <circle cx={pad+w*cell/2} cy={pad+h*cell/2} r={w*cell*.23} fill="none" stroke="#ded9ec" strokeDasharray="4 8" opacity=".55" pointerEvents="none"/>
      {Array.from({length:w},(_,x)=><text key={'col'+x} x={pad+x*cell+cell/2} y="20" className="coordinate">{String.fromCharCode(65+x)}</text>)}
      {Array.from({length:h},(_,y)=><text key={'row'+y} x="14" y={pad+y*cell+cell/2+4} className="coordinate">{y+1}</text>)}
      {s.effects.filter(e=>e.type==='zone').map((e,i)=>{const source=s.units.find(u=>u.uid===e.sourceUserId);const [x,y]=coordPoint(e.center||e.coord||source?.coord||'');const r=Number(e.radius??0);const x0=Math.max(0,x-r),y0=Math.max(0,y-r),x1=Math.min(w,x+r+1),y1=Math.min(h,y+r+1);return x>=0&&<g key={'zone'+i} pointerEvents="none"><rect x={pad+x0*cell} y={pad+y0*cell} width={Math.max(0,x1-x0)*cell} height={Math.max(0,y1-y0)*cell} rx="8" fill={source?.team==='X'?'#f1c8a8':'#c8b8e9'} fillOpacity=".24" stroke={source?.team==='X'?'#cd9674':'#a994cf'} strokeDasharray="6 4"/><text x={pad+x*cell+cell/2} y={pad+y*cell+8} className="zone-label">{e.spellName}</text></g>;})}
      {Array.from({length:w*h},(_,i)=>{const x=i%w,y=Math.floor(i/w),coord=String.fromCharCode(65+x)+(y+1);return <rect className="board-cell" key={coord} x={pad+x*cell} y={pad+y*cell} width={cell} height={cell} fill={s.map.obstacles[coord]?'#dcdde7':moveMode&&options.cells.includes(coord)?'#ded4ee66':'transparent'} onClick={()=>selectCell(coord)} role="button" tabIndex={moveMode?0:-1} aria-label={'位置 '+coord} onKeyDown={e=>{if(e.key==='Enter')selectCell(coord);}}><title>{coord}{s.map.obstacles[coord]?' · 障碍物':''}</title></rect>;})}
      {reps.map(u=>{const [x,y]=coordPoint(u.coord);if(x<0)return null;const c=ch(u.serial),color=c?.color||accents[3],current=s.turn?.uid===u.uid,sel=selected===u.uid;return <g key={u.uid} transform={`translate(${pad+x*cell+cell/2} ${pad+y*cell+cell/2})`} className={'board-token '+(current?'current':'')+(u.hp<=0?' defeated':'')} role="button" tabIndex={0} aria-label={`${u.name}，${u.coord}，HP ${u.hp}`} onClick={()=>selectUnit(u)} onKeyDown={e=>{if(e.key==='Enter')selectUnit(u);}}><title>{u.name} · HP {u.hp}/{u.hp_max} · {u.coord}</title>{(current||sel||options.targets.includes(u.uid))&&<circle r="27" fill="none" stroke={current?'#8571bf':options.targets.includes(u.uid)?'#bd9962':'#8796ad'} strokeWidth="1.5" strokeDasharray={current?'4 3':undefined}/>}<circle r="21" fill="white" stroke={u.team==='Y'?'#91a8cb':'#d7ac8f'} strokeWidth="2"/><circle r="17" fill={color+'20'}/><text textAnchor="middle" y="5" fill={color} fontSize="15" fontWeight="700">{u.summon?'✧':u.serial.replace('Y','')}</text><rect x="-18" y="22" width="36" height="3" rx="1.5" fill="#e5e6ec"/><rect x="-18" y="22" width={36*Math.max(0,u.hp)/Math.max(1,u.hp_max)} height="3" rx="1.5" fill={u.team==='Y'?'#739e9b':'#ce997a'}/>{u.group_count>1&&<g transform="translate(18 -18)"><circle r="9" fill={color}/><text y="3" textAnchor="middle" fill="white" fontSize="10">{u.group_count}</text></g>}{current&&<path d="M-5-35L0-30L5-35" fill="none" stroke="#8571bf" strokeWidth="2"/>}</g>;})}
    </svg>{moveMode&&<div className="board-hint"><MapPin size={15}/>选择落点，确认后移动 <button onClick={()=>setMoveMode(false)}>取消</button></div>}{skill&&<div className="board-hint"><MousePointer2 size={15}/>为「{skill.name}」选择目标 <button onClick={()=>setSkill(null)}>取消</button></div>}{giveMode&&<div className="board-hint"><CakeSlice size={15}/>选择同队角色给予蛋糕 <button onClick={()=>setGiveMode(false)}>取消</button></div>}</div>;
  }

  function UnitRow({unit:u}: {unit:Unit}) {
    const c = ch(u.serial);
    const showAi = room?.local && !room.tournament && !room.story;
    return <div className={'unit-row '+(selectedUnit?.uid===u.uid?'selected':'')} style={styleColor(c?.color||'#8d86b4')}>
      <button className="unit-row-select" onClick={()=>selectUnit(u)}><span className="unit-avatar"><Emblem name={c?.emblem} size={21}/>{c&&<img className="avatar-image" src={c.avatar} alt="" onError={e=>{e.currentTarget.style.display='none';}}/>}{u.group_count>1&&<b>{u.group_count}</b>}</span><div className="unit-row-info"><div><strong>{u.name}</strong><span>{u.coord}</span></div><i><b style={{width:Math.max(0,u.hp/u.hp_max*100)+'%'}}/></i><small>{u.hp} / {u.hp_max} <span>{u.controller==='ai'?'AI':room?.members.find(m=>m.id===u.controller)?.name||'玩家'}</span></small></div>{view?.turn?.uid===u.uid&&<span className="turn-dot"/>}</button>
      {showAi&&<AiCheckbox checked={u.controller==='ai'} label={`${u.team} 队 ${u.name} AI`} disabled={!host||busy||replay>=0||view?.state.phase==='ended'||!!view?.reaction} title={view?.reaction?'请先处理当前防御反应':'勾选交给 AI，取消后由你控制'} onChange={checked=>control({op:'assign',uid:u.uid,controller:checked?'ai':session})}/>}
    </div>;
  }

  return <RuntimeSettings.Provider value={defaults || {}}><KnowledgeProvider catalog={catalog} serial={detail?.serial} openCharacter={setDetail} openTerm={openTerm}><div className={'app-shell'+(page==='story/play'?' story-playing':page==='story/editor'?' story-authoring':'')}>
    <aside className="sidebar"><button className="brand-mark" aria-label="星序首页" onClick={()=>navigate('home')}><Sparkles size={29} strokeWidth={1.4}/></button><div className="nav-stack">{[{id:'home',label:'战术大厅',icon:Home},{id:'story',label:'剧情模式',icon:BookOpen},{id:'setup',label:'自由对战',icon:Swords},{id:'battle',label:'当前房间',icon:Users},{id:'sim',label:'模拟实验室',icon:BarChart3},{id:'library',label:'角色图鉴',icon:BookOpen},{id:'editor',label:'角色管理',icon:Settings2},{id:'tournament',label:'锦标赛',icon:Star},{id:'settings',label:'设置',icon:Settings2}].map(n=><button key={n.id} title={n.label} aria-label={n.label} className={'nav-item '+(page===n.id?'active':'')} onClick={()=>n.id==='battle'&&!room?setDialog('join'):n.id==='setup'?prepare('local'):(setFilter(''),navigate(n.id))}><n.icon size={21} strokeWidth={1.6}/><span>{n.label}</span></button>)}</div><div className="sidebar-bottom"><IconButton title="帮助" onClick={()=>navigate('help/mechanics')}><CircleHelp size={21}/></IconButton><div className="user-avatar">{name.slice(0,1)}</div></div></aside>
    <main className="main-shell">
      <header className="topbar">
        <div className="breadcrumb"><span>星序 <small>ASTRAL TACTICS</small></span><ChevronRight size={14}/><strong>{{home:'战术大厅',setup:'作战准备',battle:'战术室',sim:'模拟实验室',library:'角色图鉴',editor:'角色管理',tournament:'锦标赛',settings:'设置',story:'章节选择','story/play':'剧情模式','story/editor':'故事创作室','help/mechanics':'机制说明','help/characters':'角色卡帮助'}[page]}</strong></div>
        <div className="topbar-actions"><span className={'connection '+(connected?'online':'')}><i/>{connected?'引擎已连接':'连接恢复中'}</span><span className="topbar-divider"/><button className="profile" onClick={()=>navigate('settings')}><span>{name.slice(0,1)}</span>{name}<ChevronDown size={13}/></button></div>
      </header>
    {error&&<div className="error-banner" role="alert"><Shield size={17}/><span>{error}</span><button onClick={()=>setError('')} aria-label="关闭错误"><X size={16}/></button></div>}
    {!catalog.length || !defaults?<div className="loading-state"><LoaderCircle size={28} className="spin"/><h2>正在连接星序引擎</h2><p>请确认 Python 后端已启动</p><button className="button" onClick={()=>location.reload()}>重新连接</button></div>:<>
    {page==='home'&&<div className="page home-page"><div className="page-intro"><div><div className="eyebrow">{pageCopy.home.intro.eyebrow}</div><h1>{pageCopy.home.intro.welcome}{name}<span className="heading-star">✧</span></h1><p>{pageCopy.home.intro.description}</p></div><span className="date-badge"><Sun size={16}/> {pageCopy.home.intro.badge}</span></div><section className="home-hero"><div className="hero-copy"><Tag><span className="tiny-spark">✧</span> {pageCopy.home.hero.tag}</Tag><h2>{pageCopy.home.hero.title[0]}<br/>{pageCopy.home.hero.title[1]}<span>{pageCopy.home.hero.punctuation}</span></h2><p>{pageCopy.home.hero.description[0]}<br/>{pageCopy.home.hero.description[1]}</p><button className="button primary" onClick={()=>navigate('tournament')}>{pageCopy.home.hero.button} <ArrowRight size={17}/></button><small><Diamond size={11}/> {pageCopy.home.hero.features}</small></div><div className="hero-art" aria-hidden="true"><div className="orbital orbit-one"/><div className="orbital orbit-two"/><span className="hero-star star-one">✦</span><span className="hero-star star-two">✧</span><span className="hero-star star-three">✦</span><div className="hero-card hero-card-back"><Sigil character={ch('Y7')}/><strong>{pageCopy.home.hero.backCard.name}</strong><small>{pageCopy.home.hero.backCard.caption}</small></div><div className="hero-card hero-card-front"><div className="hero-card-label"><span>VIII</span><Sparkles size={15}/></div><Sigil character={ch('Y8')}/><strong>{pageCopy.home.hero.frontCard.name}</strong><small>{pageCopy.home.hero.frontCard.caption}</small><div className="hero-card-stars">✧ &nbsp; ✧ &nbsp; ✧</div></div><span className="art-caption">{pageCopy.home.hero.caption}</span></div></section>
      <div className="mode-grid">{[{...pageCopy.home.modes.story,icon:BookOpen,color:'violet',click:()=>navigate('story')},{...pageCopy.home.modes.local,icon:Swords,color:'violet',click:()=>prepare('local')},{...pageCopy.home.modes.lan,icon:Users,color:'peach',click:()=>prepare('lan')},{...pageCopy.home.modes.watch,icon:Eye,color:'sage',click:()=>prepare('watch')}].map(m=><button key={m.en} className={'mode-card '+m.color} onClick={m.click}><span className="mode-icon"><m.icon size={24} strokeWidth={1.5}/></span><div><small>{m.en}</small><h3>{m.title}</h3><p>{m.desc}</p></div><ArrowRight size={18}/></button>)}</div>
      <section className="roster-section"><div className="section-heading"><div><span className="eyebrow">{pageCopy.home.roster.eyebrow}</span><h2>{pageCopy.home.roster.title} <span className="count">{catalog.length}</span></h2></div><button className="text-button" onClick={()=>navigate('library')}>{pageCopy.home.roster.button} <ArrowRight size={16}/></button></div><div className="character-grid home-roster">{['Y1','Y4','Y7','Y8','Y9','Y12'].map(s=>ch(s)).filter(Boolean).map(c=><CharacterCard key={c!.serial} character={c!} onClick={()=>setDetail(c!)}/>)}</div></section><footer className="page-footer"><span><Sparkles size={13}/> {pageCopy.home.footer.tagline}</span><button onClick={()=>setDialog('join')}>{pageCopy.home.footer.join} <ArrowRight size={13}/></button></footer></div>}

    {page==='setup'&&<div className="page setup-page"><div className="page-intro"><div><div className="eyebrow">{pageCopy.setup.eyebrow}</div><h1>{pageCopy.setup.title}</h1><p>{pageCopy.setup.description}</p></div><button className="button subtle" onClick={()=>setDialog('join')}><Link size={16}/>加入房间</button></div><div className="setup-toolbar"><div className="segmented">{[['local','本地对战',Swords],['lan','局域网房间',Users],['watch','AI 观战',Eye]].map(([id,label,Icon])=><button key={id as string} className={mode===id?'active':''} onClick={()=>setMode(id as string)}>{typeof Icon!=='string'&&<Icon size={16}/>} {label as string}</button>)}</div><label>地图尺寸 <select value={mapSize} onChange={e=>setMapSize(e.target.value)}>{Array.from(new Set([mapSize,...[8,10,12,16].map(n=>`${n}x${n}`)])).map(size=><option key={size}>{size}</option>)}</select></label></div><TeamEditor/><div className="setup-submit"><div><Sparkles size={18}/><p>{mode==='watch'?'双方将由 AI 控制。开始后可暂停、单步推进和调整速度。':mode==='lan'?'创建后可分配角色给好友，所有玩家准备后开始。':'默认由你操作双方。勾选角色旁的 AI 可开启混合对战。'}</p></div><button className="button primary" disabled={busy||!teamA.length||!teamB.length} onClick={create}>{busy?<LoaderCircle className="spin" size={16}/>:<Swords size={17}/>}创建{mode==='lan'?'房间':'演习'}<ArrowRight size={16}/></button></div></div>}

    {page==='library'&&<div className="page"><div className="page-intro"><div><span className="eyebrow">{pageCopy.library.eyebrow}</span><h1>角色图鉴</h1><p>{pageCopy.library.description}</p></div><input className="search" placeholder="搜索姓名、编号或定位…" value={filter} onChange={e=>setFilter(e.target.value)}/></div><div className="character-grid library-grid">{catalog.filter(c=>(c.name+c.serial+c.role).toLowerCase().includes(filter.toLowerCase())).map(c=><CharacterCard key={c.serial} character={c} onClick={()=>setDetail(c)}/>)}</div></div>}

    {page==='battle'&&room&&view&&<div className="battle-page"><div className="battle-heading"><div><span className="eyebrow">{room.local?pageCopy.battle.localEyebrow:pageCopy.battle.lanEyebrow} · {room.code}</span><h1>{room.name}<Tag>{isPrep?'准备阶段':view.state.phase==='ended'?'演习结束':'第 '+view.state.round+' 回合'}</Tag>{replay>=0&&<Tag className="replay-tag">历史回顾</Tag>}</h1></div><div><button className="button subtle compact-button" onClick={()=>setDialog('room')}><Users size={15}/>{room.members.length} 人 · 房间</button>{!room.local&&<IconButton title="复制邀请链接" onClick={copyLink}><Copy size={17}/></IconButton>}<IconButton title="离开房间" onClick={()=>setDialog('leave')}><X size={19}/></IconButton></div></div>
      <div className="initiative-bar"><span className="initiative-label"><Clock3 size={15}/>行动序列</span><div className="initiative-list">{view.initiative.map((e,i)=>{const u=view.units.find(u=>u.uid===(e.baseUserId||e.userId));const c=ch(u?.serial||'');return <button key={e.userId} className={i===view.state.activeIndex?'active':''} onClick={()=>setSelected(u?.uid||'')} style={styleColor(c?.color||'#8b83a9')}><Emblem name={c?.emblem} size={18}/><span>{u?.name||e.name}</span>{i===view.state.activeIndex&&<small>行动中</small>}{e.isSummon&&<small>×{u?.group_count||1}</small>}</button>;})}</div><span className="initiative-round">ROUND <b>{String(view.state.round).padStart(2,'0')}</b></span></div>
      <div className="battle-layout"><aside className="team-panel panel"><div className="panel-title"><span><Users size={15}/>参战阵容</span><MoreHorizontal size={17}/></div>{['Y','X'].map(t=><div className="battle-team" key={t}><div className="team-caption"><span className={'team-dot '+(t==='X'?'orange':'')}/><strong>{t==='Y'?'星辉小队':'晨曦小队'}</strong><span>{view.units.filter(u=>u.team===t&&!u.summon&&u.hp>0).length} 存活</span></div>{view.units.filter(u=>u.team===t&&(!u.summon||u.uid===u.representative)).map(u=><UnitRow key={u.uid} unit={u}/>)}</div>)}<div className="team-panel-bottom"><Shield size={16}/><span>{pageCopy.battle.tagline}</span></div></aside>
      <section className="arena-panel panel"><div className="arena-heading"><div><Compass size={16}/><strong>星光演习场</strong><span>{view.map.width} × {view.map.height}</span></div><div><span className="board-legend"><i/>星辉 <i/>晨曦</span><IconButton title="棋盘说明" onClick={()=>setDialog('help')}><CircleHelp size={16}/></IconButton></div></div><Board snapshot={view}/><div className="arena-status"><span><MousePointer2 size={13}/>{isPrep?'选择角色后可调整站位':canAct?'选择技能或移动，规划本次行动':view.reaction?'等待防御反应':view.state.phase==='ended'?'演习已结束，可回顾战斗过程':'等待当前角色行动'}</span><span>点击单位查看状态</span></div></section>
      <aside className="detail-panel panel"><div className="tabs detail-tabs"><button className={logTab==='log'?'active':''} onClick={()=>setLogTab('log')}>战斗记录</button><button className={logTab==='unit'?'active':''} onClick={()=>setLogTab('unit')}>角色详情</button><button className={logTab==='grave'?'active':''} onClick={()=>setLogTab('grave')}>墓地</button></div>{logTab==='log'?<><div className="log-heading"><span><i className="live-dot"/> {replay>=0?'历史事件':'实时战报'}</span><input aria-label="筛选战报" placeholder="筛选事件…" value={logFilter} onChange={e=>setLogFilter(e.target.value)}/></div><div className="event-log">{[...history].filter(e=>e.output&&(!logFilter||e.output.includes(logFilter))&&(replay<0||e.id<=history[replay]?.id)).map(e=><details open className={'log-event '+e.kind} key={e.id}><summary><span className="event-icon">{e.kind==='reaction'?<Shield size={13}/>:e.kind==='ai'?<Zap size={13}/>:<Sparkles size={13}/>}</span><div><small>ROUND {String(e.snapshot.state.round).padStart(2,'0')} <span>#{e.id}</span></small><p>{e.output.split('\n').find(l=>l.trim())}</p></div></summary><pre>{e.output}</pre><button className="text-button" onClick={()=>setReplay(history.findIndex(h=>h.id===e.id))}>回到此刻 <RotateCcw size={12}/></button></details>)}</div></>:logTab==='grave'?<div className="grave-list">{view.graveyard.length?view.graveyard.map(g=><button key={g.uid} onClick={()=>skill&&preview(skill.command,g.uid,skill.name)}><Flower2 size={20}/><strong>{g.name}</strong><small>第 {g.round} 回合阵亡</small>{skill?.revive_candidates.includes(g.uid)&&<Tag>可复活</Tag>}</button>):<div className="empty-state"><Flower2 size={30}/><p>{pageCopy.battle.emptyGraveyard}</p><small>阵亡单位将在这里显示</small></div>}</div>:selectedUnit?<div className="unit-detail"><div className="unit-detail-header" style={styleColor(ch(selectedUnit.serial)?.color||'#8c83b1')}><span className="unit-avatar"><Emblem name={ch(selectedUnit.serial)?.emblem} size={28}/></span><div><small>{selectedUnit.serial||'SUMMON'}</small><h3>{selectedUnit.name}</h3></div><Tag>{selectedUnit.coord}</Tag></div><Resource label="HP · 体力" value={selectedUnit.hp} max={selectedUnit.hp_max}/><Resource label="MP · 魔力" value={selectedUnit.mp} max={selectedUnit.mp_max} kind="mp"/><Resource label="SAN · 理智" value={selectedUnit.san} max={100} kind="san"/><div className="detail-stat"><span>剩余移动力</span><strong>{selectedUnit.move_remaining}</strong></div><div className="detail-stat"><span>阶段</span><strong>{selectedUnit.phase}</strong></div>{selectedUnit.spirit!=null&&<div className="detail-stat"><span>战意值</span><strong>{selectedUnit.spirit}</strong></div>}<h4>状态与增益</h4><div className="status-tags">{selectedUnit.buffs.map((b,i)=><Tag key={i}>{b.spellName||b.auxType||b.type} {b.remainingRounds?`· ${b.remainingRounds} 回合`:''}</Tag>)}{!selectedUnit.statuses.length&&!selectedUnit.buffs.length&&<span className="muted">暂无状态效果</span>}</div>{selectedUnit.summon&&<><h4>召唤组成员 · {selectedUnit.group_count}</h4><div className="summon-members">{view.units.filter(u=>u.representative===selectedUnit.representative).map(u=><button key={u.uid} onClick={()=>selectUnit(u)} className={selected===u.uid?'active':''}><span>{u.name}</span><small>{u.hp} / {u.hp_max} HP</small></button>)}</div></>}<h4>个人物品</h4>{selectedUnit.inventory.length?selectedUnit.inventory.map((item,i)=><div className="inventory-row" key={i}><CakeSlice size={16}/>{item.item} <b>×{item.count}</b></div>):<span className="muted">背包空空，轻装上阵</span>}</div>:null}</aside></div>
      <AutoReactionControls room={room} session={session} live={replay<0} onRoom={updateRoom} onError={setError}/>
      {view.reaction&&<section className="reaction-panel"><div className="reaction-icon"><Shield size={26}/></div><div><span className="eyebrow">{pageCopy.battle.reactionEyebrow}</span><h3>{view.reaction.defender_name}，选择你的应对方式</h3><p>{view.reaction.attacker} 使用「{view.reaction.skill}」{view.reaction.damage&&' · 伤害骰 '+view.reaction.damage}{view.reaction.roll!=null&&' · D100 '+view.reaction.roll}</p></div><div className="reaction-options"><BonusSelect value={bonus} change={setBonus}/>{view.reaction.choices.map(c=><button className="button" key={c} disabled={busy||replay>=0||view.reaction?.controller!==session} onClick={()=>react(c)}>{c==='dodge'?<Feather size={16}/>:c==='counter'?<Swords size={16}/>:<Shield size={16}/>}{{dodge:'闪避',counter:'反击',block:'格挡'}[c]}</button>)}{view.reaction.controller!==session&&<span>等待对应玩家处理</span>}</div></section>}
      {view.state.phase==='ended'?<section className="result-banner"><Sparkles size={28}/><div><span className="eyebrow">{pageCopy.battle.result.eyebrow}</span><h2>{view.result?.winner==='Y'?'星辉小队':view.result?.winner==='X'?'晨曦小队':'本次演习'}获胜</h2><p>第 {view.state.round} 回合 · {pageCopy.battle.result.tagline}</p></div><button className="button" onClick={()=>setReplay(0)}><RotateCcw size={16}/>回顾战斗</button><button className="button primary" disabled={busy || !!room.tournament && !room.tournament.next_available} title={room.tournament && !room.tournament.next_available ? '锦标赛已结束' : undefined} onClick={room.story ? (room.story.finished ? ()=>navigate('story/play') : ()=>control({op:'play'})) : room.tournament ? nextTournamentGame : ()=>prepare('local')}>{room.story ? (room.story.finished ? '返回剧情' : '继续下一波') : room.tournament ? '下一场' : '再来一场'} <ArrowRight size={16}/></button></section>:<section className="action-dock panel"><div className="acting-character" style={styleColor(ch(activeUnit?.serial||'')?.color||'#8a78c5')}><span className="unit-avatar"><Emblem name={ch(activeUnit?.serial||'')?.emblem} size={30}/></span><div><small>{isPrep?'准备阶段':'当前行动'}</small><h3>{activeUnit?.name||'等待开始'}</h3><span><b>{view.turn?.actions['主动']||0}</b> 主动作 <i/> <b>{view.turn?.actions['附加']||0}</b> 附加</span></div></div>{isPrep?<div className="prep-message"><Sparkles size={20}/><div><strong>一切准备就绪了吗？</strong><p>可在房间面板分配角色，或选择棋子后调整站位。</p></div><button className="button" disabled={busy||selectedUnit?.controller!==session} onClick={()=>{setMoveMode(!moveMode);setSkill(null);}}>调整站位</button>{host?<button className="button primary" disabled={busy} onClick={()=>control({op:'start'})}>开始演习 <ArrowRight size={16}/></button>:<button className="button primary" disabled={busy} onClick={()=>control({op:'ready',ready:!room.members.find(m=>m.id===session)?.ready})}>{room.members.find(m=>m.id===session)?.ready?'取消准备':'我已准备'}</button>}</div>:<><div className="skill-strip">{view.turn?.skills.map(s=><button className={'skill-button '+(skill?.index===s.index?'selected':'')} key={s.index} disabled={busy||!canAct||!s.available} title={s.reason||s.effects.map(e=>Object.entries(e).filter(([k])=>['伤害骰','回复hp','回复mp','作用半径','辅助效果'].includes(k)).map(([k,v])=>k+': '+v).join(' · ')).join('\n')} onClick={()=>{setSkill(s);setMoveMode(false);setGiveMode(false);if(s.revive_candidates.length)setLogTab('grave');}}><span className="skill-icon">{s.index===0?<Sword size={22}/>:s.once?<Star size={22}/>:<Sparkles size={22}/>}</span><strong>{s.name}</strong>{s.effects.some(e=>e['伤害骰'])&&<span className="skill-effect">{s.effects.find(e=>e['伤害骰'])?.['伤害骰']}</span>}<small>{s.reason||((s.command.startsWith('.a')?'附加':'主动作')+' · '+(s.formula?'动态费用':s.mp_cost+' MP'))}</small></button>)}</div><div className="utility-actions"><button disabled={busy||!canAct} className={moveMode?'active':''} onClick={()=>{setMoveMode(!moveMode);setSkill(null);setGiveMode(false);}}><MapPin size={17}/>移动</button><button disabled={busy||!canAct} onClick={()=>preview('.a eat','','食用蛋糕')}><CakeSlice size={17}/>食用</button><button disabled={busy||!canAct} className={giveMode?'active':''} onClick={()=>{setGiveMode(!giveMode);setSkill(null);setMoveMode(false);}}><Heart size={17}/>给予</button></div><button className="end-turn button" disabled={busy||!canAct} onClick={()=>preview('.i end','','结束当前回合')}>结束回合<ArrowRight size={16}/></button></>}</section>}
      <CombatControls room={room} session={session} live={replay<0} onRoom={updateRoom} onError={setError} bonus={bonus} setBonus={setBonus} timing={timing} setTiming={setTiming} failedReaction={failedReaction} setFailedReaction={setFailedReaction}/>
      {skill&&<div className="skill-selection-note"><Sparkles size={15}/><span>已选择「{skill.name}」：点击棋子指定目标，或使用技能的默认目标。</span><button onClick={()=>preview(skill.command,'',skill.name)}>使用默认目标 <ArrowRight size={13}/></button><button onClick={()=>setSkill(null)}>取消</button></div>}
      <div className="playback-bar"><div><span className="eyebrow">{replay>=0?pageCopy.battle.reviewEyebrow:pageCopy.battle.liveEyebrow}</span>{replay>=0?<><IconButton title="上一步" disabled={replay<=0} onClick={()=>setReplay(i=>i-1)}><ArrowLeft size={17}/></IconButton><IconButton title="下一步" disabled={replay>=history.length-1} onClick={()=>setReplay(i=>i+1)}><StepForward size={18}/></IconButton><span>事件 {replay+1} / {history.length}</span></>:<><IconButton title={room.running?'暂停 AI':'播放 AI'} disabled={!host||busy||isPrep} onClick={()=>control({op:room.running?'pause':'play'})}>{room.running?<CirclePause size={20}/>:<CirclePlay size={20}/>}</IconButton><IconButton title="AI 单步" disabled={!host||busy||isPrep} onClick={()=>control({op:'step'})}><StepForward size={18}/></IconButton><select aria-label="AI 速度" disabled={!host} value={room.speed} onChange={e=>control({op:'speed',speed:Number(e.target.value)})}>{[1,2,4].map(n=><option value={n} key={n}>{n}× 速度</option>)}</select></>}</div><input aria-label="战斗事件时间线" type="range" min="0" max={Math.max(0,history.length-1)} value={replay<0?Math.max(0,history.length-1):replay} onChange={e=>setReplay(Number(e.target.value))}/><button className="text-button" onClick={()=>setReplay(-1)}><Radio size={14}/>{replay>=0?'回到实时':'正在实时'}</button></div>
    </div>}

    <StoryApp page={page} session={session} catalog={catalog} room={room} onNavigate={navigate} onRoom={r=>acceptRoom(r)} onDirty={setSettingsDirty} renderCharacter={c=><CharacterCard character={c} small onClick={()=>setDetail(c)}/>}/>
    {page==='sim'&&<div className="page sim-page"><SimulationPanel teamA={teamA} teamB={teamB} catalog={catalog} onError={setError}/><TeamEditor sim/></div>}
    {page==='editor'&&<DataEditor onError={setError} onSaved={reloadCatalog}/>}
    {page==='settings'&&<SettingsPage preferences={{name,reduced}} onPreferences={updatePreferences} onSaved={setDefaults} onDirty={setSettingsDirty} catalog={catalog} onError={setError}/>}
    {page.startsWith('help/')&&<HelpPage tab={page.split('/')[1]} catalog={catalog} navigate={navigate} anchor={location.hash.slice(1)}/>}
    {page==='battle'&&!room&&<div className="page"><h1>尚未加入战术室</h1><p className="muted">输入房间码加入已有房间，或创建一场自由对战。</p><div className="modal-actions"><button className="button" onClick={()=>setDialog('join')}>输入房间码</button><button className="button primary" onClick={()=>prepare('local')}>自由对战</button></div></div>}
    {page==='tournament'&&<TournamentPanel catalog={catalog} onError={setError} onWatch={r=>acceptRoom(r)} onSettings={()=>{navigate('settings');location.hash='playoff-settings';}}/>}
    </>}
    </main>
    {toast&&<div className="toast" role="status"><Check size={16}/>{toast}</div>}
    {draft&&<Modal title="确认本次行动" onClose={()=>setDraft(null)}><div className="confirmation-art"><Sparkles size={32}/></div><h3 className="confirmation-title">{draft.label}</h3><p className="confirmation-target">作用目标：{draft.targetName}</p><p className="muted confirmation-target">{draft.cost!=null?'基础消耗 '+draft.cost+' MP · ':''}随机费用与骰点在执行时结算</p><div className="modal-note"><Shield size={17}/>确认后执行行动，并同步给房间内所有玩家。</div>{room?.revision!==draft.revision&&<p className="error-text">战况已更新，请取消后重新选择行动。</p>}<div className="modal-actions"><button className="button" onClick={()=>setDraft(null)}>取消</button><button className="button primary" disabled={busy||room?.revision!==draft.revision} onClick={send}>{busy?<LoaderCircle className="spin" size={16}/>:<Check size={16}/>}确认行动</button></div></Modal>}
    {detail&&<Modal title="角色百科" onClose={()=>setDetail(null)} wide className="encyclopedia-modal">
      <div className={"character-profile"+(term?" with-definition":"")} style={styleColor(detail.color)}>
        <div className="profile-sidebar">
          <div className="profile-art"><Sigil character={detail}/><span>{detail.serial}</span><h2>{detail.name}</h2><p><TermText text={detail.role}/></p></div>
          <CharacterStats character={detail}/>
        </div>
        <div className="profile-skills"><span className="eyebrow">{pageCopy.library.encyclopediaEyebrow}</span><h3>技能百科</h3>
          <section><h4 className="encyclopedia-section-title">角色特性与普通攻击</h4>
            <div className="trait-definitions">{detail.traits.map(t=><div key={t.name} data-definition-owner={"trait:"+t.name}><h5><TermText text={t.name}/></h5><EncyclopediaProse content={t.description} sourceKey={"trait:"+t.name} data={{character:detail,spell:{}}}/></div>)}</div>
            <EncyclopediaProse content={detail.basic} data={{character:detail,spell:{}}}/>
          </section>
          {detail.passives.length>0&&<section className="encyclopedia-passives"><h4 className="encyclopedia-section-title">角色被动与专属规则</h4><EncyclopediaProse content={detail.passives} data={{character:detail,spell:{}}}/></section>}
          <h4 className="encyclopedia-section-title">技能</h4>
          {detail.skills.map((s,i)=><details key={s.id} data-skill-index={i} data-definition-owner={"skill:"+s.id} open>
            <summary><span className="library-emblem skill-emblem" aria-hidden="true" style={{maskImage:`url(${s.icon})`,WebkitMaskImage:`url(${s.icon})`}}/><strong><TermText text={s.name}/></strong><Tag><TermText text={s.timing}/></Tag><ChevronDown size={15}/></summary>
            <EncyclopediaSkill skill={s} data={{character:detail,spell:detail.spells.find(spell=>spell.name===s.name&&(spell.phase??0)===s.phase)??{}}}/>
          </details>)}
        </div>
        {term&&<DefinitionPanel id={term} onClose={closeTerm} onOpen={openTerm} onHelp={anchor=>navigate('help/mechanics',false,anchor)}/>}
      </div>
    </Modal>}
    {dialog==='join'&&<Modal title="加入好友的战术室" onClose={()=>setDialog('')}><p className="muted">连接同一局域网，并使用房主的 IP 地址打开此页面。</p><label className="field">你的称呼<input value={name} maxLength={20} onChange={e=>setName(e.target.value)}/></label><label className="field">房间码<input className="code-input" placeholder="例如 A3B8C2" maxLength={6} value={joinCode} onChange={e=>setJoinCode(e.target.value.toUpperCase())}/></label><label className="checkbox"><input type="checkbox" checked={spectator} onChange={e=>setSpectator(e.target.checked)}/><Eye size={16}/>以观众身份加入</label><div className="modal-actions"><button className="button primary" disabled={busy||joinCode.trim().length!==6} onClick={join}>加入战术室 <ArrowRight size={16}/></button></div></Modal>}
    {dialog==='room'&&room&&<Modal title={'房间 · '+room.code} onClose={()=>setDialog('')} wide><div className="room-members">{room.members.map(m=><div key={m.id}><span className="user-avatar">{m.name[0]}</span><strong>{m.name}</strong><Tag>{m.id===room.host?'房主':m.spectator?'观众':'玩家'}</Tag><span className={m.ready?'ready-state':'muted'}>{m.ready?'已准备':'未准备'}</span></div>)}</div><h3>角色控制权</h3><p className="muted">房主可将角色交给玩家或 AI。进行中的防御反应处理完毕后才能调整。</p><div className="assignment-list">{room.snapshot.units.filter(u=>!u.summon||u.uid===u.representative).map(u=><label key={u.uid}><span className={'team-dot '+(u.team==='X'?'orange':'')}/><strong>{u.name}</strong><span>{u.team} 队 · {u.serial}</span><select disabled={!host||busy||!!room.snapshot.reaction||room.story?.control==='NONE'} value={u.controller} onChange={e=>control({op:'assign',uid:u.uid,controller:e.target.value})}><option value="ai">AI 控制</option>{room.members.filter(m=>!m.spectator).map(m=><option value={m.id} key={m.id}>{m.name}</option>)}</select></label>)}</div><div className="modal-actions">{!room.local&&<button className="button" onClick={copyLink}><Copy size={15}/>复制邀请链接</button>}<button className="button primary" onClick={()=>setDialog('')}>完成 <Check size={15}/></button></div></Modal>}

    {dialog==='leave'&&<Modal title="离开本次演习？" onClose={()=>setDialog('')}><p>房主离开会暂停 AI 推进；其他玩家离开后，其角色交由房主接管。服务运行期间可通过原房间码重新加入。</p><div className="modal-actions"><button className="button" onClick={()=>setDialog('')}>留在这里</button><button className="button primary" onClick={leave}>离开房间 <ArrowRight size={16}/></button></div></Modal>}
    {dialog==='invite'&&<Modal title="邀请好友" onClose={()=>setDialog('')}><p>请选择好友能够访问的局域网地址。多网卡或 VPN 环境下，请选择与好友处于同一网络的地址。</p><div className="invite-links">{room?.invite_urls?.map(url=><label key={url}><input readOnly aria-label="局域网邀请地址" value={url}/><button className="icon-button" title="复制地址" onClick={async()=>{try{await navigator.clipboard.writeText(url);notice('邀请链接已复制');}catch{notice('请选中地址并手动复制');}}}><Copy size={15}/></button></label>)}</div><label className="field">房间码<input readOnly value={room?.code||''}/></label></Modal>}
  </div></KnowledgeProvider></RuntimeSettings.Provider>;
}

createRoot(document.getElementById('root')!).render(<App/>);
