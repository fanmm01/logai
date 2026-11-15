import { useEffect, useRef, useState } from 'react';
import { api, type Effect } from './types';
import './playoffs.css';

export type PlayoffConfig = { format: string; draw: string; display: string; reset_final: boolean; round_bo: Record<string, number> };
export const defaultPlayoff: PlayoffConfig = { format: 'playin12', draw: 'weighted', display: 'graph', reset_final: false, round_bo: {} };
export const playoffLabels: Record<string, string> = {
  playin12: '12 进 8（现有）', groups16: '16 进 8 · 四队双循环六轮', hybrid12: '12 队 · 前八双败、后四单败',
  double16: '16 强双败', single16: '16 强单败', playin18: '前十四直进 · 15–18 名小组附加赛', groups18: '18 队 · 三组六队双循环十轮',
  nba16: '仿 NBA · 两半区前六直进＋三进二附加赛',
};
export const drawLabels: Record<string, string> = { random: '纯随机抽签', tiered: '分档抽签', ranked: '按成绩排列（奇数／偶数分半）', weighted: '现有加权抽签（12 进 8）' };

export function sourceLabel(source: number | string, team: (id: number) => string) {
  if (typeof source === 'number') return team(source);
  if (source.startsWith('D:')) return `晋级种子 ${Number(source.slice(2)) + 1}（待小组赛结束）`;
  if (source.startsWith('N:')) return `半区第 ${source.slice(3)} 位（附加赛晋级）`;
  return source.slice(2) + (source.startsWith('W:') ? ' 胜者' : ' 负者');
}

export function PlayoffGraph({ series, display, team, preview = false, selected = '', onSelect }: {
  series: Effect[]; display: string; team: (id: number) => string; preview?: boolean; selected?: string; onSelect: (s: Effect) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [paths, setPaths] = useState<{ id: string; d: string }[]>([]);
  const [size, setSize] = useState([0, 0]);
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    function update() {
      if (!el) return;
      const bounds = el.getBoundingClientRect();
      const lines: { id: string; d: string }[] = [];
      if (display === 'graph') for (const s of series) for (const field of ['a', 'b']) {
        if (typeof s[field] !== 'string' || s[field].startsWith('D:')) continue;
        const from = el.querySelector<HTMLElement>(`[data-series="${s[field].slice(2)}"]`);
        const to = el.querySelector<HTMLElement>(`[data-series="${s.id}"]`);
        if (!from || !to) continue;
        const a = from.getBoundingClientRect(), b = to.getBoundingClientRect();
        const x1 = a.right - bounds.left + el.scrollLeft, y1 = a.top + a.height / 2 - bounds.top + el.scrollTop;
        const x2 = b.left - bounds.left + el.scrollLeft, y2 = b.top + b.height / 2 - bounds.top + el.scrollTop;
        lines.push({ id: s.id + field, d: `M${x1},${y1} C${(x1+x2)/2},${y1} ${(x1+x2)/2},${y2} ${x2},${y2}` });
      }
      setPaths(lines); setSize([el.scrollWidth, el.scrollHeight]);
    }
    update(); const observer = new ResizeObserver(update); observer.observe(el);
    return () => observer.disconnect();
  }, [series, display]);
  return <div className={'playoff-graph ' + (display === 'list' ? 'playoff-list' : 'bracket-tree')} ref={root}>
    {display === 'graph' && <svg className="bracket-edges" width={size[0]} height={size[1]} aria-hidden="true">{paths.map(p => <path key={p.id} d={p.d}/>)}</svg>}
    {[...new Set(series.map(s => s.round || s.stage))].map(round => <div className="bracket-stage" key={round}><h3>{series.find(s => (s.round || s.stage) === round)?.stage}</h3>
      {series.filter(s => (s.round || s.stage) === round).map(s => <button type="button" key={s.id} data-series={s.id} className={'series-card ' + (selected === (preview ? s.round : s.id) ? 'selected' : '')} onClick={() => onSelect(s)}>
        <strong>{s.id} · BO{s.best_of}</strong>
        {(['a', 'b'] as const).map((key, i) => <span key={key}>{!preview && s['team_' + key] != null ? team(s['team_' + key]) : sourceLabel(s[key], team)} {!preview && <b>{s.score[i]}</b>}</span>)}
        <small>{preview ? '点击设置本轮 BO' : s.skipped ? '无需重置，已跳过' : s.winner != null ? `${team(s.winner)} 晋级` : s.conditional ? '败者组冠军赢下总决赛后进行' : s.team_a != null && s.team_b != null ? '可进行' : '等待前序比赛'}</small>
        {s.games?.some((g: Effect) => g.manual) && <small>含人工接管场次</small>}
      </button>)}
    </div>)}
  </div>;
}

export function PlayoffSettings({ value, change, bo, onError, hideFormat = false }: {
  value: PlayoffConfig; change: (v: PlayoffConfig) => void; bo: Record<string, number>; onError: (message: string) => void; hideFormat?: boolean;
}) {
  const [graph, setGraph] = useState<Effect | null>(null);
  const [round, setRound] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const boKey = JSON.stringify(bo), configKey = JSON.stringify(value);
  useEffect(() => {
    let cancelled = false; setLoading(true); setError('');
    const timer = setTimeout(() => {
      api<Effect>('/tournaments/playoff-preview', { playoff: value, bo }).then(data => { if (!cancelled) setGraph(data); })
        .catch(e => { if (!cancelled) { setError(e.message); onError(e.message); } })
        .finally(() => { if (!cancelled) setLoading(false); });
    }, 150);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [boKey, configKey]);
  const current = graph?.series.find((s: Effect) => s.round === round);
  const overrideKey = value.format + '/' + round;
  return <div className="playoff-settings">
    <div className="playoff-controls">
      {!hideFormat && <label>季后赛赛制<select aria-label="季后赛赛制" value={value.format} onChange={e => { setRound(''); change({ ...value, format: e.target.value, draw: value.draw === 'weighted' && e.target.value !== 'playin12' ? 'tiered' : value.draw }); }}>{Object.entries(playoffLabels).map(([k, text]) => <option value={k} key={k}>{text}</option>)}</select></label>}
      <label>分组与捉对<select aria-label="分组与捉对" value={value.draw} onChange={e => change({ ...value, draw: e.target.value })}>{Object.entries(drawLabels).filter(([k]) => k !== 'weighted' || value.format === 'playin12').map(([k, text]) => <option value={k} key={k}>{text}</option>)}</select></label>
      <label>赛图显示<select aria-label="赛图显示" value={value.display} onChange={e => change({ ...value, display: e.target.value })}><option value="graph">晋级关系图</option><option value="list">按轮次列表</option></select></label>
      {['hybrid12', 'double16'].includes(value.format) && <label className="checkbox"><input type="checkbox" checked={value.reset_final} onChange={e => change({ ...value, reset_final: e.target.checked })}/>败者组冠军获胜后重置总决赛</label>}
    </div>
    <p className="muted">预览以常规赛名次代替队名。抽签随赛事种子确定；小组晋级后重新捉对，首轮尽量回避同组重赛。小组同分依次比较系列赛胜场、净胜局、胜局数、常规赛排名。</p>
    {value.format === 'groups18' && <p>三组六队双循环十轮；每队十场，每组三十场。各组前二和最好的两个小组第三进入八强。</p>}
    {value.format === 'nba16' && <p>常规赛排名分成两个半区，每区前六直进；第 7–10 名争夺另两个席位。半区有三支附加赛队时采用 7/8 先赛、败者对第 9 名；有四支时采用 7/8、9/10，再由前一场败者对后一场胜者。每区只有八队时，第 7、8 名直接晋级。抽签方式控制分半：随机分半、相邻名次分档抽签，或依名次奇偶分半。</p>}
    {['groups16', 'playin18'].includes(value.format) && <p>四队小组采用双循环六轮，每队六场，每组十二场；各组前二晋级。</p>}
    <p className="muted">一般 BO：首轮 {bo.BO_N_PLAYIN}，其余轮次 {bo.BO_N_QF}，半决赛／胜败者组决赛 {bo.BO_N_SF}，总决赛／铜牌赛 {bo.BO_N_FINAL}。点击任一空比赛格可独立设置其所在轮次。</p>
    {loading && <p role="status">正在更新赛图…</p>}{error && <p role="alert">{error}</p>}
    {graph && !error && <PlayoffGraph series={graph.series} display={value.display} preview team={n => `常规赛第 ${n+1} 名`} selected={round} onSelect={s => setRound(s.round)}/>}
    {current && !loading && !error && <div className="playoff-round-editor" aria-label="逐轮 BO 编辑器"><strong>{current.stage}</strong><span>{value.round_bo[overrideKey] == null ? '继承一般设置' : '独立 BO'}</span>
      <label>本轮 BO<input aria-label="本轮 BO" type="number" min={1} step={2} value={value.round_bo[overrideKey] ?? current.best_of} onChange={e => { const n = e.target.valueAsNumber; if (Number.isInteger(n) && n > 0 && n % 2 === 1) change({ ...value, round_bo: { ...value.round_bo, [overrideKey]: n } }); }}/></label>
      <button type="button" className="button" disabled={value.round_bo[overrideKey] == null} onClick={() => { const next = { ...value.round_bo }; delete next[overrideKey]; change({ ...value, round_bo: next }); }}>恢复继承</button>
      <small>本轮所有比赛使用同一 BO；其他轮次保持各自设置。</small>
    </div>}
  </div>;
}
