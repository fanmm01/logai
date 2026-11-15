import { useEffect, useRef, useState } from 'react';
import { Redo2, Undo2 } from 'lucide-react';
import { api, requestId, type Character, type Effect, type Room } from './types';
import { useRuntimeSettings } from './settings';
import { linkPaneScrolling } from './paneScrolling';
import { CharacterLink } from './KnowledgeUI';
import { PlayoffGraph, defaultPlayoff, playoffLabels, drawLabels } from './Playoffs';

type Feedback = { onError: (message: string) => void };
const rid = requestId;
const labels: Record<string, string> = { dodge: '闪避', counter: '反击', block: '格挡', name: '名称', serial: '编号',
  attrs: '数值属性', str_attrs: '字符串属性', spells: '技能', effects: '技能效果', inventory: '初始物品',
  timing: '发动时机', category: '技能类别', phase: '阶段', type: '效果类型', pre_transformed: '特殊初始变身状态',
  skill_id: '技能唯一编号', skill_index: '固定技能编号',
  default_persist: '默认持续', cooldown_rounds: '冷却回合', _once_per_battle: '每场限一次' };
Object.assign(labels, { GLOBAL_RANGE_OVERRIDE:'全局攻击射程覆盖', _mp_formula:'特殊魔力费用公式',
  _mp_formula_transformed:'变身后魔力费用骰式', initial_phase:'初始阶段', cost_blood_ml:'消耗血液（毫升）',
  cost_hp_pct_current:'消耗当前生命比例', cost_hp_dice:'生命费用骰式', cost_mp_pct_max:'消耗最大魔力比例',
  cost_mp_pct_current:'消耗当前魔力比例', cost_counter_type:'消耗计数器类型', cost_counter_amount:'计数器费用',
  weapon_required:'所需武器形态', weapon_state:'武器形态', counter_required:'所需计数器', counter_type:'计数器类型',
  max_value:'计数器上限', variant:'特殊防护种类', on_enter_mp_drain_pct:'入场魔力吸取比例',
  on_enter_attr_debuff:'入场属性削减', on_enter_trigger_rate:'入场触发概率', ally_safe:'伤害豁免友方',
  hp_thresholds:'生命触发阈值', target_phase:'目标阶段', linked_summon:'关联召唤物', exclude:'排除列表',
  chant_interruptible:'吟唱可打断', prep_free:'准备阶段免费使用', item:'物品名称', count:'数量' });
const backupReasons:Record<string,string> = { manual:'手动备份', 'before save':'保存前自动备份',
  'before settings save':'设置保存前自动备份', 'before restore':'恢复前自动备份', 'before training weights':'权重替换前自动备份', 'before browser upgrade':'升级前版本' };

export function AutoReactionControls({ room, session, live, onRoom, onError }: Feedback & {
  room: Room; session: string; live: boolean; onRoom: (r: Room) => void;
}) {
  const units = room.snapshot.units.filter(u => u.controller === session && (!u.summon || u.uid === u.representative));
  const [uid, setUid] = useState('');
  const unit = units.find(u => u.uid === uid) || units.find(u => u.uid === room.snapshot.reaction?.defender) || units[0];
  const [choice, setChoice] = useState<string>(useRuntimeSettings().AUTO_REACTION_CHOICE);
  const defaults = useRuntimeSettings();
  const [count, setCount] = useState<number>(defaults.AUTO_REACTION_COUNT);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const intent = useRef(0);
  const policy = unit?.auto_reaction;
  useEffect(() => { setChoice(unit?.auto_reaction?.choice || defaults.AUTO_REACTION_CHOICE); }, [unit?.uid]);
  async function submit(op: string) {
    if (!unit) return;
    const attempt = ++intent.current;
    const setter = op === 'start' ? setStarting : setStopping;
    setter(true);
    try {
      const result = await api<Room>(`/rooms/${room.code}/auto-reaction`, {
        op, uid: unit.uid, choice, count, revision: policy?.revision || 0, request_id: rid(),
      });
      onRoom(result);
    } catch (e) { if (attempt === intent.current) onError((e as Error).message); }
    finally { setter(false); }
  }
  const editable = live && !!unit && room.snapshot.state.phase !== 'ended';
  const stoppable = !!unit && room.snapshot.state.phase !== 'ended';
  return <section className="auto-controls panel" aria-label="自动反应控制">
    <div><h3>自动反应</h3><p role="status">{policy?.enabled ? `已开启 · ${labels[policy.choice]} · 剩余 ${policy.remaining} 次` : '已停止 · 手动选择反应'}</p></div>
    <label>控制单位<select aria-label="自动反应单位" value={unit?.uid || ''} disabled={!stoppable || starting || stopping} onChange={e => setUid(e.target.value)}>
      {!units.length && <option value="">没有可控制单位</option>}{units.map(u => <option key={u.uid} value={u.uid}>{u.team} 队 · {u.name}{u.summon ? '（召唤组）' : ''}</option>)}
    </select></label>
    <label>反应方式<select aria-label="自动反应方式" value={choice} disabled={!editable || starting || stopping} onChange={e => setChoice(e.target.value)}>
      {(unit?.reaction_choices || ['dodge', 'counter']).map(c => <option key={c} value={c}>{labels[c]}</option>)}
    </select></label>
    <label>次数<input aria-label="自动反应次数" type="number" min="1" max="999" value={count} disabled={!editable || starting || stopping} onChange={e => setCount(Number(e.target.value))}/></label>
    <button className="button primary" disabled={!editable || starting || stopping || !!policy?.enabled} onClick={() => submit('start')}>{starting ? '正在开启…' : '开启自动反应'}</button>
    <button className="button stop-button" disabled={!stoppable || stopping || (!policy?.enabled && !starting)} onClick={() => submit('stop')}>{stopping ? '正在停止…' : '停止自动反应'}</button>
    <small>非本人回合也可操作。停止后，当前单次结算完成，后续反应交回手动；预设不适用时停止并提示。{!live && '当前为历史回顾，停止控件仍作用于实时战斗。'}</small>
  </section>;
}

type EnumMap = Record<string, Record<string, string>>;
const defaultSpell = () => ({ name: '新技能', timing: '2', category: 1, phase: 0, 消耗mp: 0, effects: [defaultEffect()] });
const defaultEffect = () => ({ type: 1, 客体: 4, 伤害骰: '1d6', 成功率: 70, 射程: 1, 可闪避性: 1, 可反击性: 1 });

// Use type-specific fields immediately when the effect selector changes.
function switchEffectType(value: Effect, type: number, samples: Record<string, any>): Effect {
  const defaults: Record<number, Effect> = {
    1: defaultEffect(),
    2: { type, 客体: 1, 护盾值: '1d8', 持续回合: 2 },
    3: { type, 客体: 1, 回复hp: '1d6', 回复mp: 0 },
    4: { type, 客体: 1, 辅助效果: '伤害骰加值', 辅助效果值: 1, 持续回合: 1 },
    7: { type, 客体: 1, 引发目标法术: 1, 引发延迟回合: 1 },
    8: { type, 客体: 35, 作用半径: 1, 持续回合: 3, 伤害骰: '1d6', 领域中心跟随: 1 },
    10: { type, 客体: 1, 回复总量: 6, 持续回合: 3 },
  };
  const template = structuredClone(defaults[type] || samples.__effectTemplates?.[type] || { type, 客体: 1 });
  for (const key of Object.keys(template)) {
    if (key !== 'type' && Object.hasOwn(value, key)) template[key] = structuredClone(value[key]);
  }
  template.type = type;
  if (Object.hasOwn(value, '_disabled')) template._disabled = value._disabled;
  return template;
}

function AddField({ add, suggestions }: { add: (key: string) => void; suggestions: string[] }) {
  const [key, setKey] = useState('');
  const id = useRef('fields-' + rid());
  return <div className="add-field"><input aria-label="新增属性名称" placeholder="属性名称" list={id.current} value={key} onChange={e => setKey(e.target.value)}/>
    <datalist id={id.current}>{suggestions.map(k => <option key={k} value={k}/>)}</datalist>
    <button type="button" className="button subtle" disabled={!key.trim() || key.trim() === '_disabled'} onClick={() => { add(key.trim()); setKey(''); }}>添加属性</button></div>;
}

export function Fields({ value, change, name = '', enums, samples }: {
  value: any; change: (v: any) => void; name?: string; enums: EnumMap; samples: Record<string, any>;
}) {
  if (Array.isArray(value)) return <div className="field-list">{value.map((v, i) => <details className={'editor-block ' + (['effects', 'spells'].includes(name) && v?._disabled ? 'disabled-effect' : '')} key={v?.skill_id || i} open>
    <summary>{labels[name] || name} {name === 'spells' ? v?.skill_index ?? '待分配' : i + 1}{v?.name ? ` · ${v.name}` : ''}{['effects', 'spells'].includes(name) && v?._disabled && ' · 已禁用'}</summary>
    {name === 'effects' && <div className="effect-enable"><label><input type="checkbox" aria-label={`启用效果 ${i + 1}`} checked={!v?._disabled} onChange={e => change(value.map((x, j) => j === i ? {...x, _disabled: !e.target.checked} : x))}/>启用此效果</label><small>保存后生效；禁用效果以注释保留在 Python 文件中，可继续编辑和重新启用。</small></div>}
    <Fields name={name === 'spells' ? 'spell' : name === 'effects' ? 'effect' : 'entry'} value={v} change={next => change(value.map((x, j) => j === i ? next : x))} enums={enums} samples={samples}/>
    <button className="text-button danger-text" onClick={() => change(value.filter((_, j) => j !== i))}>删除{labels[name] || '此项'}</button>
    {name === 'spells' && <button className="text-button" style={{ marginLeft: 16 }} onClick={() => change(value.map((x, j) => j === i ? { ...x, _disabled: !x._disabled } : x))}>{v?._disabled ? '启用技能' : '禁用技能'}</button>}
  </details>)}<button className="button subtle" onClick={() => change([...value, name === 'spells' ? defaultSpell() : name === 'effects' ? defaultEffect() : name === 'inventory' ? { item: '蛋糕HP', count: 1 } : {}])}>新增{labels[name] || '条目'}</button></div>;
  const choices = samples.__byType?.[name] || samples;
  if (value && typeof value === 'object') return <div className="field-table"><div className="field-table-heading"><span>属性</span><span>值</span></div>{Object.entries(value).filter(([key]) => !['_disabled', 'next_skill_index'].includes(key)).map(([key, v]) => <div className="field-row" key={key}>
    <div><label>{labels[key] || key}</label>{!['skill_id', 'skill_index'].includes(key) && <button aria-label={'删除属性 ' + key} className="field-remove" onClick={() => { const next = { ...value }; delete next[key]; change(next); }}>×</button>}</div>
    {['skill_id', 'skill_index'].includes(key) ? <output aria-label={labels[key]}>{String(v)}</output> : v !== null && typeof v === 'object' ? <details open={['spells', 'effects'].includes(key)}><summary>{Array.isArray(v) ? `${v.length} 项` : `${Object.keys(v).length} 个属性`}</summary><Fields value={v} name={key} change={next => change({ ...value, [key]: next })} enums={enums} samples={samples}/></details>
      : enums[key] ? <select aria-label={labels[key] || key} value={String(v)} onChange={e => change(name === 'effect' && key === 'type' ? switchEffectType(value, Number(e.target.value), samples) : { ...value, [key]: typeof v === 'number' ? (/^-?\d+$/.test(e.target.value) ? Number(e.target.value) : e.target.value) : e.target.value })}>
        {!Object.hasOwn(enums[key], String(v)) && <option value={String(v)}>{String(v)}</option>}{Object.entries(enums[key]).map(([k, text]) => <option key={k} value={k}>{text} ({k})</option>)}</select>
        : typeof v === 'boolean' ? <input aria-label={labels[key] || key} type="checkbox" checked={v} onChange={e => change({ ...value, [key]: e.target.checked })}/>
          : <input aria-label={labels[key] || key} type={typeof v === 'number' ? 'number' : 'text'} step="any" value={v == null ? '' : String(v)} onChange={e => change({ ...value, [key]: typeof v === 'number' ? Number(e.target.value) : e.target.value })}/>}
  </div>)}<AddField suggestions={Object.keys(choices).filter(k => !(k in value) && !k.startsWith('__') && !['_disabled', 'skill_id', 'skill_index', 'next_skill_index'].includes(k))} add={key => change({ ...value, [key]: structuredClone(choices[key] ?? (name === 'attrs' ? 0 : '')) })}/></div>;
  return <input value={String(value ?? '')} onChange={e => change(typeof value === 'number' ? Number(e.target.value) : e.target.value)}/>;
}

export function DataEditor({ onError, onSaved }: Feedback & { onSaved: () => void }) {
  const [data, setData] = useState<Effect | null>(null);
  const undoStack = useRef<Effect[]>([]);
  const redoStack = useRef<Effect[]>([]);
  const editorLayout = useRef<HTMLDivElement>(null);
  const [index, setIndex] = useState(0);
  const [showDisabled, setShowDisabled] = useState(false);
  const [tab, setTab] = useState('characters');
  const [backups, setBackups] = useState<Effect[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [dirty, setDirty] = useState(false);
  const [training, setTraining] = useState<Effect | null>(null);
  const [trainingTargets, setTrainingTargets] = useState<string[]>([]);
  const [allowlist, setAllowlist] = useState('');
  const [allowlistBusy, setAllowlistBusy] = useState(false);
  const [savedAllowlist,setSavedAllowlist]=useState('');
  const load = async () => {
    const selected = data?.characters[index]?.serial;
    const d = await api<Effect>('/data');
    const found = d.characters.findIndex((ch: Effect) => ch.serial === selected);
    setIndex(found >= 0 ? found : Math.max(0, d.characters.findIndex((ch: Effect) => d.enabled_serials.includes(ch.serial))));
    if (found >= 0 && !d.enabled_serials.includes(selected)) setShowDisabled(true);
    setData(d); setDirty(false);
    undoStack.current = []; redoStack.current = [];
    setBackups((await api<{ backups: Effect[] }>('/backups')).backups);
    const access = await api<{ allowlist: string[] }>('/allowlist');
    setAllowlist(access.allowlist.join('\n'));
    setSavedAllowlist(access.allowlist.join('\n'));
  };
  async function work(fn: () => Promise<void>) { setBusy(true); try { await fn(); } catch (e) { onError((e as Error).message); } finally { setBusy(false); } }
  useEffect(() => { work(load); api<{ jobs: Effect[] }>('/training').then(x => setTraining(x.jobs[0] || null)).catch(() => {}); }, []);
  useEffect(() => { if (training?.status !== 'running') return; const timer = setInterval(() => api<Effect>('/simulations/' + training.id).then(setTraining).catch(e => onError(e.message)), 1000); return () => clearInterval(timer); }, [training?.id, training?.status]);
  useEffect(() => { if (training?.status === 'completed') { api<Effect>('/data').then(latest => setData(old => old ? { ...old, needs_training: latest.needs_training } : latest)); api<{backups:Effect[]}>('/backups').then(x => setBackups(x.backups)); } }, [training?.id, training?.status]);
  useEffect(() => {
    if (editorLayout.current) return linkPaneScrolling(editorLayout.current);
  }, [tab, !!data]);
  const saveLock = useRef(false);
  const saveData = async () => {
    if (!data || busy || saveLock.current || !dirty || !data.characters.some((ch: Effect) => data.enabled_serials.includes(ch.serial))) return;
    saveLock.current=true;
    try { await work(async () => { const result = await api<Effect>('/data/save', data); setMessage('已保存，并自动备份：' + result.backup); await load(); onSaved(); }); }
    finally { saveLock.current=false; }
  };
  const saveAllowlist = async () => {
    if(allowlistBusy || saveLock.current || allowlist===savedAllowlist)return;
    saveLock.current=true;setAllowlistBusy(true);
    try { const result=await api<{allowlist:string[]}>('/allowlist',{allowlist});setAllowlist(result.allowlist.join('\n'));setSavedAllowlist(result.allowlist.join('\n'));setMessage('访问白名单已保存'); }
    catch(e){onError((e as Error).message);}finally{saveLock.current=false;setAllowlistBusy(false);}
  };
  useEffect(()=>{
    const handler=(event:KeyboardEvent)=>{
      if (!(event.ctrlKey || event.metaKey)) return;
      if (event.key.toLowerCase() === 's') {
        event.preventDefault(); if (event.repeat) return;
        if (tab === 'allowlist') void saveAllowlist(); else void saveData();
        return;
      }
      if (!data || tab === 'allowlist') return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.closest('input, textarea, select') || target.isContentEditable)) return;
      const key = event.key.toLowerCase();
      const undo = key === 'z' && !event.shiftKey;
      const redo = (key === 'y' && !event.shiftKey) || (key === 'z' && event.shiftKey);
      if (undo && undoStack.current.length) {
        event.preventDefault();
        redoStack.current.push(structuredClone(data));
        setData(undoStack.current.pop()!); setDirty(true);
      } else if (redo && redoStack.current.length) {
        event.preventDefault();
        undoStack.current.push(structuredClone(data));
        setData(redoStack.current.pop()!); setDirty(true);
      }
    };
    window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler);
  });
  if (!data) return <div className="page"><h1>角色管理</h1><p>正在读取数据…</p></div>;
  const samples: Record<string, any> = {};
  function collect(value: any) { if (Array.isArray(value)) value.forEach(collect); else if (value && typeof value === 'object') Object.entries(value).forEach(([k, v]) => { samples[k] ??= v; collect(v); }); }
  collect(data.characters);
  samples.__effectTemplates = {};
  for (const character of data.characters) for (const spell of character.spells || []) for (const effect of spell.effects || []) {
    const previous = samples.__effectTemplates[effect.type];
    if (!previous || Object.keys(effect).length < Object.keys(previous).length) {
      samples.__effectTemplates[effect.type] = Object.fromEntries(Object.entries(effect).filter(([key]) => key !== '_disabled'));
    }
  }
  samples.__byType = data.fields;
  const edit = (next: Effect) => {
    if (!data) return;
    undoStack.current.push(structuredClone(data));
    if (undoStack.current.length > 100) undoStack.current.shift();
    redoStack.current = [];
    setData(next); setDirty(true);
  };
  const characters = data.characters as Effect[];
  const enabled = new Set<string>(data.enabled_serials);
  const enabledCount = characters.filter(ch => enabled.has(ch.serial)).length;
  const selectedTrainingTargets = characters.filter(ch => enabled.has(ch.serial) && trainingTargets.includes(ch.serial)).map(ch => ch.serial);
  const selectedCharacter = characters[index];
  const missingTemplates = [...new Set<string>((selectedCharacter?.spells || []).filter((s: Effect) => !s._disabled).flatMap((s: Effect) => (s.effects || []).filter((e: Effect) => !e._disabled && e['召唤物模板'] && !Object.hasOwn(data.summons, e['召唤物模板'])).map((e: Effect) => e['召唤物模板'])))];
  const addCharacter = () => { const reserved = new Set([...data.reserved, ...characters.map(c => c.serial)]); let n = 1; while (reserved.has('Y' + n)) n++; edit({ ...data, enabled_serials: [...data.enabled_serials, 'Y' + n], characters: [...characters, { name: '新角色', serial: 'Y' + n, attrs: { 体力: 10, 体力上限: 10, 魔力: 10, 魔力上限: 10, 敏捷: 50, 闪避: 25, 斗殴: 50, 行动力: 8, 等级: 1 }, str_attrs: { 伤害值: '1d6' }, inventory: [], spells: [defaultSpell()] }] }); setIndex(characters.length); };
  const roster = (active: boolean) => characters.map((ch, i) => enabled.has(ch.serial) === active && <button type="button" className={'roster-character ' + (i === index ? 'active' : '')} key={i} onClick={() => setIndex(i)}>{ch.serial} · {ch.name}{!active && <small>已禁用</small>}</button>);
  const deleteCharacter = () => {
    const remaining = characters.filter((_, i) => i !== index);
    const nextIndex = Math.max(0, remaining.findIndex(ch => enabled.has(ch.serial)));
    edit({ ...data, enabled_serials: data.enabled_serials.filter((s: string) => s !== selectedCharacter.serial), characters: remaining });
    setIndex(nextIndex);
    if (remaining[nextIndex] && !enabled.has(remaining[nextIndex].serial)) setShowDisabled(true);
  };
  return <div className="page management-page"><div className="section-heading"><div><h1>角色管理</h1><p className="muted">基础版统一数据 · 属性—值编辑 · {dirty ? '有未保存修改' : '已同步'}{data.needs_training ? ' · 权重需要重新训练' : ''}</p></div>
    <button className="button primary" disabled={busy || !dirty || enabledCount === 0} onClick={saveData}>保存</button><div className="expansion-toolbar management-history"><button className="button" aria-label="&#25764;&#38144;" title="&#25764;&#38144; (Ctrl/&#8984;+Z)" disabled={!undoStack.current.length} onClick={() => { if (!data) return; redoStack.current.push(structuredClone(data)); setData(undoStack.current.pop()!); setDirty(true); }}><Undo2 size={16}/></button><button className="button" aria-label="&#37325;&#20570;" title="&#37325;&#20570; (Ctrl+Y / &#8984;+Shift+Z)" disabled={!redoStack.current.length} onClick={() => { if (!data) return; undoStack.current.push(structuredClone(data)); setData(redoStack.current.pop()!); setDirty(true); }}><Redo2 size={16}/></button></div></div>
    <div className="tabs"><button onClick={() => setTab('characters')}>魔法少女</button><button onClick={() => setTab('summons')}>召唤物模板</button><button onClick={() => setTab('items')}>物品模板</button><button onClick={() => setTab('backups')}>备份与训练</button><button onClick={() => setTab('allowlist')}>访问白名单</button></div>
    {data.settings?.GLOBAL_RANGE_OVERRIDE === 1 && <p className="muted">基础数据当前开启全局射程覆盖（攻击射程为 99）；可在“设置 · 全局规则”中关闭。领域半径仍按各效果计算。</p>}
    {message && <p className="save-message" role="status">{message}</p>}
    {enabledCount === 0 && <p className="danger-text" role="alert">至少保留一名启用角色才能保存。</p>}
    {tab === 'characters' ? <div className="editor-layout" ref={editorLayout}><aside className="panel editor-roster"><div className="roster-group"><h3>已启用（{enabledCount}）</h3>{roster(true)}</div><details className="roster-group disabled-roster" open={showDisabled} onToggle={e => setShowDisabled(e.currentTarget.open)}><summary>已禁用（{characters.length - enabledCount}）</summary>{roster(false)}</details><button className="button" onClick={addCharacter}>新建角色</button></aside>
      <section className="panel editor-content">{selectedCharacter && <><div className="character-enable"><label><input aria-label="启用此角色" type="checkbox" checked={enabled.has(selectedCharacter.serial)} onChange={e => {
          edit({ ...data, enabled_serials: e.target.checked ? [...data.enabled_serials, selectedCharacter.serial] : data.enabled_serials.filter((s: string) => s !== selectedCharacter.serial) });
          if (!e.target.checked) setShowDisabled(true);
        }}/><strong>启用此角色</strong><span>{enabled.has(selectedCharacter.serial) ? '已启用' : '已禁用'}</span></label><p>保存后生效。禁用角色保留全部数据，可继续编辑和重新启用；不参与新的编队、模拟及训练。</p>{missingTemplates.length > 0 && <p className="danger-text">启用前需修正未定义的召唤物模板：{missingTemplates.join('、')}</p>}</div><Fields value={selectedCharacter} name="character" enums={data.enums} samples={samples} change={next => edit({ ...data, enabled_serials: data.enabled_serials.map((s: string) => s === selectedCharacter.serial ? next.serial : s), characters: characters.map((ch, i) => i === index ? next : ch) })}/>
        <button className="button danger-text" onClick={deleteCharacter}>删除整个角色（保存后生效）</button></>}</section></div>
      : tab === 'backups' ? <section className="panel editor-content"><h2>备份与重新训练</h2><p>保存、恢复和权重替换前自动备份。未保存修改不会进入训练。</p><div className="expansion-toolbar">
        <button className="button" disabled={busy} onClick={() => work(async () => { await api('/backups', {}); setBackups((await api<{ backups: Effect[] }>('/backups')).backups); })}>立即备份</button>
        <button className="button primary" disabled={busy || dirty || training?.status === 'running' || enabledCount < 6} onClick={() => work(async () => setTraining(await api<Effect>('/training', {})))}>重新训练全部启用角色</button></div>
        <fieldset className="training-selection" disabled={busy || dirty || training?.status === 'running'}><legend>选择重训角色（已选 {selectedTrainingTargets.length} 名）</legend><div className="training-targets">{characters.filter(ch => enabled.has(ch.serial)).map(ch => <label key={ch.serial}><input type="checkbox" checked={selectedTrainingTargets.includes(ch.serial)} onChange={e => setTrainingTargets(old => e.target.checked ? [...old, ch.serial] : old.filter(s => s !== ch.serial))}/>{ch.serial} · {ch.name}</label>)}</div>
        <p className="muted">所选角色在同一任务中共同训练，从零重建单人、团队及关联召唤物策略。共享召唤物策略更新也会影响其他使用者；其他角色权重保持原值。</p>
        <button className="button primary" disabled={!selectedTrainingTargets.length || enabledCount < 6} onClick={() => work(async () => setTraining(await api<Effect>('/training', { target_serials: selectedTrainingTargets })))}>重训所选角色</button></fieldset>
        {dirty && <p className="muted">请先保存修改，再启动训练。</p>}
        {enabledCount < 6 && <p className="muted">默认训练包含 3 对 3 抽样，需要至少启用 6 名角色。</p>}
        {training && <><p role="status">训练：{training.status}{training.message && ' · ' + training.message}</p><p>训练目标：{training.params?.target_serials?.map((s: string) => `${s} · ${characters.find(ch => ch.serial === s)?.name || s}`).join('、') || '全部启用角色'}</p>{training.params?.enabled_serials && <p>本次固定参战角色池：{training.params.enabled_serials.map((s: string) => `${s} · ${characters.find(ch => ch.serial === s)?.name || s}`).join('、')}</p>}{training.result?.summons && <p>已重训关联召唤物：{training.result.summons.join('、') || '无'}</p>}{training.status === 'completed' && training.params?.target_serials && <p className="muted">指定角色训练完成；全局待训练标记保留，请在需要时执行全量训练。</p>}{training.result?.preserved_characters?.length > 0 && <p>已保留且未重训的角色表：{training.result.preserved_characters.join('、')}</p>}<pre className="verbose-log">{training.logs?.join('\n')}</pre></>}
        <div className="backup-list">{backups.map(b => <div key={b.id}><span>{new Date(b.time * 1000).toLocaleString()} · {backupReasons[b.reason] || b.reason}</span><a className="button subtle" href={`/api/ui/backups/${b.id}/download`}>下载</a><button className="button" disabled={busy} onClick={() => { if (confirm('恢复此备份？当前版本会先自动备份。')) work(async () => { await api(`/backups/${b.id}/restore`, {}); await load(); onSaved(); setMessage('备份已恢复'); }); }}>恢复</button></div>)}</div></section>
        : tab === 'allowlist' ? <section className="panel editor-content"><h2>数据管理访问白名单</h2><p>每行填写 IP 地址或 CIDR。调试时输入 *.*.*.* 可允许所有 IPv4/IPv6 地址，请勿在公网长期启用。</p><label className="allowlist-editor">白名单<textarea rows={8} spellCheck={false} value={allowlist} onChange={e => setAllowlist(e.target.value)} placeholder={'192.168.1.6\\n*.*.*.*'}/></label><div className="expansion-toolbar"><button className="button primary" disabled={allowlistBusy} onClick={saveAllowlist}>保存白名单</button></div></section>
        : <section className="panel editor-content"><Fields value={data[tab]} name={tab} enums={data.enums} samples={samples} change={next => edit({ ...data, [tab]: next })}/></section>}
  </div>;
}

function RankingResults({ result }: { result: Effect }) {
  if (result.matrix) return <div className="ranking-panels">
    {result.rankings && <section><h3>平均积分排名</h3><div className="table-scroll"><table><thead><tr><th>排名</th><th>队伍</th><th>平均胜率</th></tr></thead><tbody>{result.rankings.map((r:any[], i:number)=><tr key={i}><td>{i+1}</td><td>{r[0]}</td><td>{(r[1]*100).toFixed(1)}%</td></tr>)}</tbody></table></div></section>}
    {Object.entries(result.contribution || {}).map(([key, raw])=><section key={key}><h3>{{algo1:'同赛制胜率差',algo2:'相对基准贡献',algo3:'归一化胜率贡献'}[key] || key}</h3><div className="table-scroll"><table><thead><tr><th>排名</th><th>角色</th><th>贡献</th></tr></thead><tbody>{(raw as any[][]).map((r,i)=><tr key={i}><td>{i+1}</td><td>{r[1]} ({r[0]})</td><td>{r[2]?.toFixed(4) ?? '数据不足'}</td></tr>)}</tbody></table></div></section>)}
  </div>;
  return <div className="ranking-panels">{Object.entries(result.rankings || {}).map(([fmt,raw])=><section key={fmt}><h3>{fmt} · 多轮预赛平均排名</h3><div className="table-scroll"><table><thead><tr><th>排名</th><th>队伍</th><th>平均积分</th><th>平均胜／场</th><th>胜率</th></tr></thead><tbody>{(raw as any[][]).map((r,i)=><tr key={i}><td>{i+1}</td><td>{r[0]}</td><td>{r[1].toFixed(2)}</td><td>{r[2].toFixed(1)} / {r[3].toFixed(1)}</td><td>{r[4].toFixed(1)}%</td></tr>)}</tbody></table></div></section>)}</div>;
}

export function SimulationPanel({ teamA, teamB, catalog, onError }: Feedback & { teamA: string[]; teamB: string[]; catalog: Character[] }) {
  const defaults = useRuntimeSettings();
  const [mode, setMode] = useState<string>(defaults.SIM_DEFAULT_MODE);
  const [format, setFormat] = useState<string>(defaults.SIM_DEFAULT_FORMAT);
  const [formats, setFormats] = useState<string[]>([defaults.SIM_DEFAULT_FORMAT]);
  const modeCount = (m: string) => m === 'verbose' ? 1 : defaults[m === 'table' ? 'SIM_TABLE_COUNT' : m === 'prelim' ? 'SIM_PRELIM_COUNT' : 'SIM_SINGLE_COUNT'];
  const [n, setN] = useState<number>(modeCount(defaults.SIM_DEFAULT_MODE));
  const [seed, setSeed] = useState('');
  const [season, setSeason] = useState('');
  const [workers, setWorkers] = useState<number>(defaults.PRELIM_WORKERS);
  const [map, setMap] = useState<string>(defaults.DEFAULT_MAP_SIZE);
  const [job, setJob] = useState<Effect | null>(null);
  const [presets, setPresets] = useState<Effect[]>([]);
  const [selectedTeams, setSelectedTeams] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<{ presets: Effect[] }>('/presets').then(x => setPresets(x.presets)).catch(e => onError(e.message)); }, []);
  useEffect(() => { const id = localStorage.getItem('combat-simulation'); if (id) api<Effect>('/simulations/' + id).then(setJob).catch(() => {}); }, []);
  useEffect(() => { if (job?.status !== 'running') return; const t = setInterval(() => api<Effect>('/simulations/' + job.id).then(setJob).catch(e => onError(e.message)), 1000); return () => clearInterval(t); }, [job?.id, job?.status]);
  const teamNames = (team: string[]) => team.map(s => catalog.find(c => c.serial === s)?.name || s).join(' + ');
  const choices = format === '1v1' ? catalog.map(c => ({ team: [c.serial], valid: true })) : presets.filter(x => x.format === format);
  const result = job?.result;
  return <section className="panel simulation-expanded"><h2>模拟实验室</h2><div className="expansion-toolbar">
    <label>模式<select value={mode} onChange={e => { const m = e.target.value; setMode(m); setN(modeCount(m)); }}><option value="single">两队批量对战</option><option value="verbose">详细单场 verbose</option><option value="table">胜率矩阵 table</option><option value="prelim">多轮预赛 prelim</option></select></label>
    {mode === 'table' && <label>赛制<select value={format} onChange={e => { setFormat(e.target.value); setSelectedTeams([]); }}>{['1v1', '2v2', '3v3'].map(f => <option key={f}>{f}</option>)}</select></label>}
    {mode === 'prelim' && <div className="preset-choices">{['1v1','2v2','3v3'].map(f=><label key={f}><input type="checkbox" checked={formats.includes(f)} onChange={e=>setFormats(old=>e.target.checked?[...old,f]:old.filter(x=>x!==f))}/>{f}</label>)}</div>}
    <label>{mode === 'prelim' ? '完整预赛轮数' : mode === 'table' ? '每组场数' : '场数'}<input type="number" min="1" max="1000" disabled={mode === 'verbose'} value={n} onChange={e => setN(Number(e.target.value))}/></label>
    <label>地图<select value={map} onChange={e => setMap(e.target.value)}>{Array.from(new Set([map,...[8,10,12,16].map(n=>`${n}x${n}`)])).map(size => <option key={size}>{size}</option>)}</select></label>
    <label>种子（空为随机）<input type="number" min="0" value={seed} onChange={e => setSeed(e.target.value)}/></label>
    <label>赛季状态（空使用设置）<input type="number" min="0" max="100" value={season} onChange={e => setSeason(e.target.value)}/></label>
    {(mode === 'prelim' || mode === 'single') && <label>并发数<input type="number" min="1" max="32" value={workers} onChange={e => setWorkers(Number(e.target.value))}/></label>}
    <button className="button primary" disabled={busy || job?.status === 'running' || (mode==='prelim'&&!formats.length)} onClick={async () => { setBusy(true); try { const next = await api<Effect>('/simulations', { mode, format, formats, n, map_size: map, workers, team_a: teamA, team_b: teamB, ...(seed ? { seed: Number(seed) } : {}), ...(season === '' ? {} : {season: Number(season)}), ...(selectedTeams.length ? { teams: selectedTeams.map(t => t.split('+')) } : {}) }); setJob(next); localStorage.setItem('combat-simulation', next.id); } catch (e) { onError((e as Error).message); } finally { setBusy(false); } }}>运行模拟</button>
    {job?.status === 'running' && <button className="button stop-button" onClick={() => api<Effect>(`/simulations/${job.id}/cancel`, {}).then(setJob).catch(e => onError(e.message))}>取消模拟</button>}
  </div>
    {mode === 'table' && <details><summary>选择参赛小队（留空使用该赛制全部预设）</summary><div className="preset-choices">{choices.map((p, i) => <label key={i}><input type="checkbox" disabled={!p.valid} checked={selectedTeams.includes(p.team.join('+'))} onChange={e => setSelectedTeams(old => e.target.checked ? [...old, p.team.join('+')] : old.filter(t => t !== p.team.join('+')))}/>{teamNames(p.team)}{!p.valid && '（角色已禁用或已删除）'}</label>)}</div><button className="text-button" onClick={() => setSelectedTeams([...new Set([teamA.join('+'), teamB.join('+')])])}>使用当前编队双方</button></details>}
    {job && <div className="task-results"><p role="status">{job.status} · 已完成 {job.completed}{job.message && ' · ' + job.message}</p>
      {result?.matrix ? <><p>非对角单元格表示“行队伍战胜列队伍”的概率；对角线显示该队伍参与的所有对局平均回合数。</p><div className="table-scroll"><table><thead><tr><th>行队伍 ↓ / 列队伍 →</th>{result.teams.map((t: string, i: number) => <th key={i}>{t}</th>)}</tr></thead><tbody>{result.matrix.map((row: (number | null)[], i: number) => <tr key={i}><th>{result.teams[i]}</th>{row.map((v, j) => <td key={j}>{i === j ? result.average_rounds?.[i] == null ? '无数据' : `${result.average_rounds[i].toFixed(1)} 回合` : v == null ? '待模拟' : (v * 100).toFixed(1) + '%'}</td>)}</tr>)}</tbody></table></div><RankingResults result={result}/></>
        : result?.rankings ? <RankingResults result={result}/>
          : result?.rate_a != null ? <div className="result-metrics"><div>星辉胜率<strong>{(result.rate_a * 100).toFixed(1)}%</strong></div><div>晨曦胜率<strong>{(result.rate_b * 100).toFixed(1)}%</strong></div><div>平均回合<strong>{result.average_rounds.toFixed(1)}</strong></div></div>
            : result && <p>胜方 {result.winner} · {result.rounds} 回合</p>}
      {job.logs?.length > 0 && <details open={mode === 'verbose'}><summary>完整记录</summary><pre className="verbose-log">{job.logs.join('\n')}</pre></details>}
      {job.status === 'completed' && <div className="export-actions"><a className="button" href={`/api/ui/simulations/${job.id}/export?format=csv`}>导出 CSV</a><a className="button" href={`/api/ui/simulations/${job.id}/export`}>导出 JSON</a></div>}
    </div>}
  </section>;
}

function TournamentReplay({ tournament: t, catalog, onError }: Feedback & { tournament: Effect; catalog: Character[] }) {
  const [selected, setSelected] = useState('');
  const [replay, setReplay] = useState<Effect | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const live = !selected && !!t.current && !t.current.room && t.operation?.startsWith('turn_');
  const replayId = selected || t.last_replay?.id;
  const hpRef = useRef<HTMLPreElement>(null), logRef = useRef<HTMLPreElement>(null);
  const games = [
    ...t.results.map((g: Effect, i: number) => ({...g, label: `常规赛 第${i + 1}场`})),
    ...t.series.flatMap((s: Effect) => s.games.map((g: Effect, i: number) => ({...g, label: `${s.id} 第${i + 1}场` }))),
  ].filter((g: Effect) => g.replay_id);
  const team = (idx: number) => t.teams[idx].map((s: string) => catalog.find(c => c.serial === s)?.name || s).join(' + ');
  useEffect(() => {
    let cancelled = false;
    setReplay(null); setFailed(false);
    if (!replayId || live) { setLoading(false); return; }
    setLoading(true);
    api<Effect>(`/tournaments/${t.id}/replays/${replayId}`).then(r => { if (!cancelled) setReplay(r); })
      .catch(e => { if (!cancelled) { setFailed(true); onError(e.message); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [t.id, replayId, live]);
  const lines = live ? t.current.replay || [] : replay?.lines || [];
  const logs = live ? t.logs || [] : replay?.logs || [];
  useEffect(() => {
    if (live) for (const pre of [hpRef.current, logRef.current]) if (pre) pre.scrollTop = pre.scrollHeight;
  }, [live, lines.length, logs.length]);
  const match = live ? t.current.match : replay?.match;
  const label = live ? `${t.current.series || '常规赛'} · 动态播放` : replay ? `${replay.series || '常规赛'} 第${replay.game}场 · 回放` : '';
  return <div className="tournament-replay">
    <section className="panel"><h2>逐回合血量变化</h2>
      <div className="expansion-toolbar"><label>场次<select value={selected} onChange={e => setSelected(e.target.value)}><option value="">{live ? '当前比赛 · 动态播放' : '最近一场'}</option>{games.map((g: Effect) => <option key={g.replay_id} value={g.replay_id}>{g.label} · {team(g.match.a)} vs {team(g.match.b)}</option>)}</select></label>
        <button className="button" disabled={!t.last_replay} onClick={() => setSelected(t.last_replay.id)}>回放</button>
        {selected && <button className="button" onClick={() => setSelected('')}>返回实时</button>}
      </div>
      {match && <p className="muted">{label} · Y：{team(match.a)} vs X：{team(match.b)}</p>}
      {lines.length ? <pre ref={hpRef} className="verbose-log round-hp-log">{lines.join('\n')}</pre> : <p className="muted">{loading ? '正在读取回放…' : failed ? '回放读取失败，请重新选择场次。' : live ? '等待本回合开始…' : '模拟完成后一次性显示每回合开始时的双方血量。'}</p>}
    </section>
    <section className="panel"><h2>逐回合战斗记录</h2>
      {logs.length ? <pre ref={logRef} className="verbose-log">{logs.join('\n')}</pre> : <p className="muted">{loading ? '正在读取回放…' : live ? '等待战斗记录…' : '直接模拟仅提供血量变化；逐回合模拟会动态显示战斗记录。'}</p>}
    </section>
  </div>;
}

export function TournamentPanel({ catalog, onError, onWatch, onSettings }: Feedback & { catalog: Character[]; onWatch: (room: Room) => void; onSettings: () => void }) {
  const defaults = useRuntimeSettings();
  const [format, setFormat] = useState<string>(defaults.SIM_DEFAULT_FORMAT);
  const [tournaments, setTournaments] = useState<Effect[]>([]);
  const [id, setId] = useState('');
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [seed, setSeed] = useState('');
  const [playoffFormat, setPlayoffFormat] = useState<string>((defaults.PLAYOFF_CONFIG || defaultPlayoff).format);
  const [display, setDisplay] = useState('');
  const t = tournaments.find(t => t.id === id) || tournaments.find(t => t.format === format);
  const load = () => api<{ tournaments: Effect[] }>('/tournaments').then(x => setTournaments(x.tournaments));
  useEffect(() => { load().catch(e => onError(e.message)); const timer = setInterval(() => load().catch(() => {}), 1000); return () => clearInterval(timer); }, []);
  useEffect(() => {
    setDisplay('');
  }, [t?.id]);
  const team = (idx: number | null | undefined) => idx == null ? '待定' : t?.teams[idx].map((s: string) => catalog.find(c => c.serial === s)?.name || s).join(' + ');
  async function advance(op: string) { if (!t) return; setBusy(true); try { await api(`/tournaments/${t.id}/advance`, { op, series: selected || t.series.find((s: Effect) => s.winner == null && s.team_a != null && s.team_b != null)?.id, revision: t.revision, request_id: rid() }); await load(); } catch (e) { onError((e as Error).message); } finally { setBusy(false); } }
  async function setSpeed(speed: number) { if (!t) return; try { const updated = await api<Effect>(`/tournaments/${t.id}/advance`, { op: 'speed', speed, request_id: rid() }); setTournaments(old => old.map(item => item.id === updated.id ? updated : item)); } catch (e) { onError((e as Error).message); } }
  const buttons = [['round', '模拟本轮'], ['series', '模拟本系列赛'], ['game', '模拟本场'], ['turn_game', '逐回合模拟本场'], ['turn_playoffs', '逐回合模拟季后赛'], ['playoffs', '模拟季后赛']];
  return <div className="page tournament-page"><h1>锦标赛</h1><p className="muted">三种赛制独立推进 · 常规赛结束自动暂停 · 服务重启保留已完成赛果</p><div className="expansion-toolbar panel">
    <label>赛制<select value={format} onChange={e => { setFormat(e.target.value); setId(''); setSelected(''); }}>{['1v1', '2v2', '3v3'].map(f => <option key={f}>{f}</option>)}</select></label>
    <label>历史赛事<select value={t?.id || ''} onChange={e => { setId(e.target.value); setSelected(''); }}><option value="">选择赛事</option>{tournaments.filter(t => t.format === format).map(t => <option key={t.id} value={t.id}>{t.id} · {t.phase}</option>)}</select></label>
    <label>种子（空为随机）<input type="number" min="0" value={seed} onChange={e => setSeed(e.target.value)}/></label>
    <button className="button primary" disabled={busy} onClick={async () => { setBusy(true); try { const basePlayoff = defaults.PLAYOFF_CONFIG || defaultPlayoff; const playoff = { ...basePlayoff, format: playoffFormat, draw: basePlayoff.draw === 'weighted' && playoffFormat !== 'playin12' ? 'tiered' : basePlayoff.draw }; const result = await api<Effect>('/tournaments', { format, playoff, ...(seed ? { seed: Number(seed) } : {}) }); setId(result.id); await load(); } catch (e) { onError((e as Error).message); } finally { setBusy(false); } }}>新建锦标赛</button>
  </div>
    <section className="panel playoff-create"><h2>新赛季季后赛赛制</h2><div className="playoff-create-main"><label>季后赛赛制<select aria-label="季后赛赛制" value={playoffFormat} onChange={e => setPlayoffFormat(e.target.value)}>{Object.entries(playoffLabels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label><button type="button" className="button" onClick={onSettings}>前往设置</button></div></section>
    {t && <><p role="status">{t.phase === 'regular' ? '常规赛' : t.phase === 'playoffs' ? '季后赛' : '赛事完成'} · {t.status}{t.current?.series && ' · 当前系列赛 ' + t.current.series} · 常规赛 {t.completed}/{t.schedule.length}{t.message && ' · ' + t.message}</p>
      <div className="expansion-toolbar"><label>逐回合速度<select aria-label="逐回合速度" value={t.speed ?? 1} onChange={e => setSpeed(Number(e.target.value))}><option value={1}>1× 速度</option><option value={2}>2× 速度</option><option value={4}>4× 速度</option></select></label><button className="button stop-button" disabled={busy || t.status === 'paused'} onClick={() => advance('pause')}>暂停推进</button>
        {t.status === 'paused' && t.current && <button className="button primary" disabled={busy} onClick={() => advance('resume')}>继续当前模拟</button>}
        {t.phase === 'regular' ? <button className="button primary" disabled={busy || t.status !== 'paused'} onClick={() => advance('regular')}>模拟常规赛</button> : <>{buttons.map(([op, text]) => <button key={op} className="button" disabled={busy || t.status !== 'paused' || t.phase === 'done' || !!t.current} onClick={() => advance(op)}>{text}</button>)}
          <button className="button primary" disabled={busy || t.phase === 'done' || !['paused', 'watching'].includes(t.status)} onClick={async () => { setBusy(true); try { const result = await api<{ room: Room }>(`/tournaments/${t.id}/watch`, { series: selected || t.series.find((s: Effect) => s.winner == null && s.team_a != null && s.team_b != null)?.id, revision: t.revision }); onWatch(result.room); } catch (e) { onError((e as Error).message); } finally { setBusy(false); } }}>进场观战</button></>}
      </div><div className="tournament-layout"><section className="panel"><h2>常规赛排名</h2><div className="table-scroll"><table><thead><tr><th>排名</th><th>小队</th><th>积分</th><th>胜／场</th></tr></thead><tbody>{t.standings.map((r: Effect, i: number) => <tr key={r.id}><td>{i + 1}</td><td>{t.teams[r.id]?.map((serial:string,j:number) => { const character=catalog.find(c=>c.serial===serial); return <span key={serial}>{j>0&&' + '}{character?<CharacterLink character={character}/>:serial}</span>; })}</td><td>{r.points.toFixed(2)}</td><td>{r.wins}/{r.battles}</td></tr>)}</tbody></table></div></section>
        <section className="panel"><h2>季后赛赛图</h2>
          {t.playoff && <p>{playoffLabels[t.playoff.format]} · {drawLabels[t.playoff.draw]}</p>}
          {!t.series.length ? <p className="muted">常规赛结束后生成，首场季后赛前自动暂停。</p> : <>
            <label>当前赛图显示<select aria-label="当前赛图显示" value={display || t.playoff?.display || 'graph'} onChange={e => setDisplay(e.target.value)}><option value="graph">晋级关系图</option><option value="list">按轮次列表</option></select></label>
            {!!t.group_tables?.length && <div className="playoff-groups">{t.group_tables.map((g: Effect) => <div key={g.id}><h3>{g.id} 组{g.complete ? ' · 已结束' : ''}</h3><div className="table-scroll"><table><thead><tr><th>排名</th><th>队伍</th><th>胜／场</th><th>局分</th></tr></thead><tbody>{g.rows.map((r: Effect, i: number) => <tr key={r.id}><td>{i+1}</td><td>{team(r.id)}</td><td>{r.wins}/{r.played}</td><td>{r.game_wins}:{r.game_losses}</td></tr>)}</tbody></table></div></div>)}</div>}
            <PlayoffGraph series={t.series} display={display || t.playoff?.display || 'graph'} team={team} selected={selected} onSelect={s => setSelected(s.id)}/>
          </>}
        </section></div>
      <TournamentReplay key={t.id} tournament={t} catalog={catalog} onError={onError}/>
    </>}
  </div>;
}
