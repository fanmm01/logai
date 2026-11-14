import { useEffect, useState } from 'react';
import { Check, Plus, RotateCcw, Save, Settings2, Trash2, ArrowUp, ArrowDown } from 'lucide-react';
import { api, type Character, type Effect } from './types';
import { type Preferences, type SettingsResponse, type SettingsValues, type SettingField } from './settings';
import './settings.css';

const applyLabels = { live: '立即生效', new_task: '新任务生效', startup: '启动时读取 · 只读' };
const choiceLabels: Record<string, string> = { single: '批量对战', verbose: '详细单场', table: '胜率矩阵', prelim: '多轮预赛', dodge: '闪避', counter: '反击', block: '格挡' };
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function NumberField({ value, onChange, label, integer = false }: { value: number; onChange: (value: number) => void; label: string; integer?: boolean }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  return <input aria-label={label} type="number" step={integer ? 1 : 'any'} value={text} required onChange={e => {
    setText(e.target.value); const n = e.target.valueAsNumber; if (Number.isFinite(n)) onChange(n);
  }} onBlur={() => { if (!text || !Number.isFinite(Number(text))) setText(String(value)); }}/>;
}

function SettingInput({ id, field, value, change, characters, seasonFallback }: { id: string; field: SettingField; value: any; change: (value: any) => void; characters: Effect[]; seasonFallback: number }) {
  if (field.readonly) return <output>{typeof value === 'string' ? value : JSON.stringify(value)}{id === 'RANDOM_BONUS_1V1' && <small>已由目标场数补齐算法取代，保留兼容字段。</small>}</output>;
  if (field.choices) return <select aria-label={field.label} value={String(value)} onChange={e => change(typeof field.choices![0] === 'number' ? Number(e.target.value) : e.target.value)}>{field.choices.map(v => <option key={v} value={v}>{id.endsWith('LOG_LEVEL') ? ({10:'DEBUG',20:'INFO',30:'WARNING',40:'ERROR',50:'CRITICAL'}[Number(v)]) : id.includes('DISPLAY') ? ({0:'仅赛果',1:'逐回合',2:'完整调试'}[Number(v)]) : id === 'DEFAULT_RESTIM_MODE' ? Number(v) === 0 ? '立即公布' : '反应后一并公布' : choiceLabels[v] || v}</option>)}</select>;
  if (field.kind === 'boolean') return <label className="checkbox"><input aria-label={field.label} type="checkbox" checked={value} onChange={e => change(e.target.checked)}/>启用</label>;
  if (field.kind === 'number') return <NumberField label={field.label} value={value} onChange={change}/>;
  if (field.kind === 'matrix') return <div className="table-scroll"><table className="settings-matrix"><thead><tr><th>挑战者</th>{[1,2,3,4].map(n => <th key={n}>种子 {n}</th>)}</tr></thead><tbody>{(value as number[][]).map((row, i) => <tr key={i}><th>第 {i + 5} 名</th>{row.map((n,j) => <td key={j}><NumberField label={`第 ${i+5} 名对种子 ${j+1} 权重`} value={n} onChange={next => change(value.map((r: number[], ri: number) => ri === i ? r.map((v, ci) => ci === j ? next : v) : r))}/></td>)}</tr>)}</tbody></table></div>;
  if (field.kind === 'teams') return <div className="settings-teams">{(value as string[][]).map((team, i) => <div className="settings-team" key={i}>
    <strong>队伍 {i + 1}</strong><div className="settings-members">{team.map((serial, j) => <div key={j}><select aria-label={`队伍 ${i+1} 成员 ${j+1}`} value={serial} onChange={e => change(value.map((t: string[], ti: number) => ti === i ? t.map((s, si) => si === j ? e.target.value : s) : t))}>
      {!characters.some(c => c.serial === serial) && <option value={serial}>{serial} · 已删除</option>}{characters.map(c => <option value={c.serial} key={c.serial}>{c.serial} · {c.name}{c.disabled ? '（已禁用）' : ''}</option>)}
    </select><button type="button" className="icon-button" aria-label={`移除队伍 ${i+1} 成员 ${j+1}`} onClick={() => change(value.map((t: string[], ti: number) => ti === i ? t.filter((_, si) => si !== j) : t))}><Trash2 size={14}/></button></div>)}</div>
    <div className="settings-team-actions"><button type="button" className="text-button" disabled={team.length >= 6 || !characters.some(c => !team.includes(c.serial))} onClick={() => change(value.map((t: string[], ti: number) => ti === i ? [...t, characters.find(c => !t.includes(c.serial))!.serial] : t))}><Plus size={14}/>成员</button>
      <button type="button" className="icon-button" disabled={!i} aria-label={`上移队伍 ${i+1}`} onClick={() => { const next = [...value]; [next[i-1], next[i]] = [next[i], next[i-1]]; change(next); }}><ArrowUp size={15}/></button>
      <button type="button" className="icon-button" disabled={i === value.length-1} aria-label={`下移队伍 ${i+1}`} onClick={() => { const next = [...value]; [next[i+1], next[i]] = [next[i], next[i+1]]; change(next); }}><ArrowDown size={15}/></button>
      <button type="button" className="text-button danger-text" onClick={() => change(value.filter((_: unknown, ti: number) => ti !== i))}>删除队伍</button></div>
    {(team.some(s => characters.find(c => c.serial === s)?.disabled || !characters.some(c => c.serial === s)) || !team.length || new Set(team).size !== team.length) && <small className="danger-text">队伍包含禁用、已删除或重复角色，或队伍为空；不能用于新任务。</small>}
  </div>)}<button type="button" className="button" disabled={!characters.length} onClick={() => change([...value, [characters[0].serial]])}><Plus size={15}/>新增队伍</button><p className="muted">支持每队 1–6 人，3v3 分组保留二人及四人特殊阵容。禁用角色所在队伍会自动跳过。</p></div>;
  if (field.kind === 'season') {
    const mode = value === null ? 'random' : typeof value === 'number' ? 'uniform' : 'character';
    return <div className="settings-season"><select aria-label="赛季状态模式" value={mode} onChange={e => change(e.target.value === 'random' ? null : e.target.value === 'uniform' ? seasonFallback : {})}><option value="random">每角色随机</option><option value="uniform">统一数值</option><option value="character">按角色指定</option></select>
      {mode === 'uniform' && <NumberField label="统一赛季状态" value={value} onChange={change}/>}{mode === 'character' && <><p className="muted">留空使用“缺失赛季状态的默认值”。</p><div className="settings-season-grid">{characters.map(c => <label key={c.serial}>{c.serial} · {c.name}<input type="number" min="0" max="100" value={value[c.serial] ?? ''} onChange={e => { const next = {...value}; if (e.target.value === '') delete next[c.serial]; else next[c.serial] = Number(e.target.value); change(next); }}/></label>)}</div></>}
    </div>;
  }
  if (Array.isArray(value)) return <div className="settings-list">{value.map((v: string, i: number) => <label key={i}><input aria-label={`${field.label} ${i+1}`} value={v} onChange={e => change(value.map((x: string, j: number) => i === j ? e.target.value : x))}/><button type="button" className="icon-button" aria-label={`移除${field.label} ${i+1}`} onClick={() => change(value.filter((_: unknown, j: number) => i !== j))}><Trash2 size={14}/></button></label>)}<button type="button" className="text-button" onClick={() => change([...value, field.default[0]])}><Plus size={14}/>新增</button></div>;
  if (value && typeof value === 'object') return <div className="settings-inline">{Object.entries(value).map(([k,v]) => <label key={k}>{k}<NumberField label={`${field.label} ${k}`} value={v as number} onChange={n => change({...value, [k]: n})}/></label>)}</div>;
  return <input aria-label={field.label} value={value} onChange={e => change(e.target.value)}/>;
}

export function SettingsPage({ preferences, onPreferences, onSaved, onDirty, catalog, onError }: {
  preferences: Preferences; onPreferences: (prefs: Preferences) => Promise<void>; onSaved: (values: SettingsValues) => void; onDirty: (dirty: boolean) => void; catalog: Character[]; onError: (message: string) => void;
}) {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [draft, setDraft] = useState<SettingsValues>({});
  const [personal, setPersonal] = useState(preferences);
  const [section, setSection] = useState('个人偏好');
  const [characters, setCharacters] = useState<Effect[]>(catalog);
  const [rules, setRules] = useState<Effect | null>(null);
  const [range, setRange] = useState(false);
  const [allowlist, setAllowlist] = useState('');
  const [savedAllowlist, setSavedAllowlist] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [loadError, setLoadError] = useState('');
  const configDirty = !!data && !equal(draft, data.values);
  const dirty = configDirty || !equal(personal, preferences) || allowlist !== savedAllowlist || !!rules && range !== !!rules.settings.GLOBAL_RANGE_OVERRIDE;
  useEffect(() => { onDirty(dirty); const handler = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } }; window.addEventListener('beforeunload', handler); return () => { window.removeEventListener('beforeunload', handler); }; }, [dirty]);
  useEffect(() => () => onDirty(false), []);
  async function load() {
    setBusy(true); setLoadError('');
    try {
      const result = await api<SettingsResponse>('/settings'); setData(result); setDraft(result.values); onSaved(result.values);
      if (result.can_manage) {
        const [d, a] = await Promise.all([api<Effect>('/data'), api<{allowlist: string[]}>('/allowlist')]);
        setRules(d); setRange(!!d.settings.GLOBAL_RANGE_OVERRIDE); setCharacters(d.characters.map((c: Effect) => ({...c, disabled: !d.enabled_serials.includes(c.serial)})));
        setAllowlist(a.allowlist.join('\n')); setSavedAllowlist(a.allowlist.join('\n'));
      }
    } catch (e) { setLoadError((e as Error).message); } finally { setBusy(false); }
  }
  useEffect(() => { load(); }, []);
  async function work(fn: () => Promise<void>) { setBusy(true); setMessage(''); try { await fn(); } catch (e) { onError((e as Error).message); } finally { setBusy(false); } }
  if (!data) return <div className="page"><h1>设置</h1><p role={loadError ? 'alert' : 'status'}>{loadError || '正在读取设置…'}</p>{loadError && <button className="button" onClick={load}>重新加载</button>}</div>;
  const groups = [...new Set(Object.values(data.fields).map(f => f.group))];
  const sections = ['个人偏好', ...groups, ...(data.can_manage ? ['全局规则', '访问管理'] : [])];
  const saveConfig = () => work(async () => { const result = await api<SettingsResponse>('/settings', {version: data.version, values: draft}); setData(result); setDraft(result.values); onSaved(result.values); setMessage('服务器设置已保存；播放等待立即生效，其余规则用于新任务。'); });
  return <div className="page settings-page"><div className="section-heading"><div><span className="eyebrow">YOUR TACTICAL PREFERENCES</span><h1><Settings2 size={26}/>设置</h1><p className="muted">统一管理战术室、AI 与赛事参数。{dirty ? '有未保存修改' : '已同步'}</p></div><button className="button" disabled={busy} onClick={() => { if (!dirty || confirm('放弃未保存修改并重新加载？')) { setPersonal(preferences); load(); } }}>重新加载</button></div>
    {loadError && <p className="danger-text" role="alert">{loadError}</p>}{message && <p className="save-message" role="status"><Check size={16}/>{message}</p>}
    <div className="settings-layout"><nav className="settings-nav" aria-label="设置分类">{sections.map(s => <button key={s} className={s === section ? 'active' : ''} onClick={() => { setSection(s); setMessage(''); }}>{s}</button>)}</nav>
      <section className="panel settings-content"><h2>{section}</h2>
      {section === '个人偏好' ? <form onSubmit={e => { e.preventDefault(); work(async () => { await onPreferences(personal); setPersonal({...personal, name: personal.name.trim()}); setMessage('个人偏好已保存到当前浏览器。'); }); }}><label className="field">你的称呼<input required maxLength={20} value={personal.name} onChange={e => setPersonal({...personal, name: e.target.value})}/></label><label className="settings-row"><span><strong>减少动态效果</strong><small>保留必要反馈，减少过渡与位移动画</small></span><input type="checkbox" checked={personal.reduced} onChange={e => setPersonal({...personal, reduced: e.target.checked})}/></label><p className="muted">个人偏好仅保存在当前浏览器。当前版本无背景音乐和音效。</p><button className="button primary" disabled={busy || equal(personal, preferences)}><Save size={15}/>保存个人偏好</button></form>
      : section === '全局规则' ? <><p>沿用角色数据的版本校验与保存前备份。已有对局保留创建时的规则。</p><label className="settings-row"><span><strong>全局攻击射程覆盖</strong><small>覆盖攻击射程；领域半径仍按技能效果计算。</small></span><input aria-label="全局攻击射程覆盖" type="checkbox" checked={range} onChange={e => setRange(e.target.checked)}/></label><button className="button primary" disabled={busy || !rules || range === !!rules.settings.GLOBAL_RANGE_OVERRIDE} onClick={() => work(async () => { const saved = await api<Effect>('/data/save', {...rules, settings: {...rules!.settings, GLOBAL_RANGE_OVERRIDE: range ? 1 : 0}}); setRules({...rules!, version: saved.version, settings: {...rules!.settings, GLOBAL_RANGE_OVERRIDE: range ? 1 : 0}}); setMessage('全局规则已保存。'); })}>保存全局规则</button></>
      : section === '访问管理' ? <><p>每行一个 IP 地址或 CIDR 网段，保存后立即生效。</p><label className="field">数据管理白名单<textarea aria-label="数据管理白名单" rows={8} value={allowlist} onChange={e => setAllowlist(e.target.value)}/></label><button className="button primary" disabled={busy || allowlist === savedAllowlist} onClick={() => work(async () => { const result = await api<{allowlist: string[]}>('/allowlist', {allowlist}); setAllowlist(result.allowlist.join('\n')); setSavedAllowlist(result.allowlist.join('\n')); setMessage('访问白名单已保存。'); })}>保存白名单</button></>
      : <form onSubmit={e => { e.preventDefault(); saveConfig(); }}><div className="settings-fields"><fieldset disabled={busy || !data.can_manage}>{Object.entries(data.fields).filter(([,f]) => f.group === section).map(([key, field]) => <div className={'setting-field ' + (field.kind === 'teams' || field.kind === 'matrix' || field.kind === 'season' ? 'setting-wide' : '')} key={key}><div className="setting-label"><label>{field.label}</label><small>{applyLabels[field.apply]}</small></div><SettingInput id={key} field={field} value={draft[key]} change={value => setDraft({...draft, [key]: value})} characters={characters} seasonFallback={draft.SEASON_FALLBACK_STATUS}/><code>{key}</code></div>)}</fieldset></div>
      {data.can_manage ? <div className="settings-actions"><button className="button primary" disabled={busy || !configDirty}><Save size={15}/>保存服务器设置</button><button type="button" className="button" disabled={busy || !configDirty} onClick={() => setDraft(data.values)}>放弃服务器修改</button><button type="button" className="text-button" disabled={busy || !Object.values(data.fields).some(f => f.group === section && !f.readonly)} onClick={() => { if (confirm(`恢复“${section}”的默认值？保存后生效。`)) setDraft(old => ({...old, ...Object.fromEntries(Object.entries(data.fields).filter(([,f]) => f.group === section && !f.readonly).map(([k,f]) => [k, structuredClone(f.default)]))})); }}><RotateCcw size={14}/>恢复本组默认值</button></div> : <p className="muted">服务器设置只读。修改需要本机或白名单设备访问。</p>}
      {section === '对战默认值' && <p className="muted">自定义战斗延迟兼容旧名称 CUSTOM_BATTLE_DELAY，统一使用 DELAY_CUSTOM_TURN。</p>}
      {section === '播放与等待' && <p className="muted">锦标赛逐回合实际等待 = 基础等待 ÷ 当前倍率。设为 0 可连续推进；修改从下一次等待开始生效。</p>}
      {section === '启动与兼容信息' && <div className="settings-runtime"><p>当前 Web 服务：{data.runtime.web_address}</p>{data.runtime.settings_file && <p>配置文件：{data.runtime.settings_file}</p>}{Object.entries(data.runtime.files || {}).map(([k,v]) => <p key={k}>{data.fields[k]?.label}：{v}</p>)}</div>}</form>}
      </section></div></div>;
}
