import { useState } from 'react';
import { api, requestId, type Effect, type Room } from './types';

const suffixes = [['', '无奖惩'], ['b', '奖励骰 ×1'], ['b2', '奖励骰 ×2'], ['b3', '奖励骰 ×3'], ['p', '惩罚骰 ×1'], ['p2', '惩罚骰 ×2'], ['p3', '惩罚骰 ×3']];
export function BonusSelect({ value, change }: { value: string; change: (value: string) => void }) {
  return <select aria-label="奖惩骰" value={value} onChange={e => change(e.target.value)}>{suffixes.map(([key, text]) => <option value={key} key={key}>{text}</option>)}</select>;
}

export function CombatControls({ room, session, live, onRoom, onError, bonus, setBonus, timing, setTiming, failedReaction, setFailedReaction }: {
  room: Room; session: string; live: boolean; onRoom: (r: Room) => void; onError: (error: string) => void;
  bonus: string; setBonus: (s: string) => void;
  timing: number; setTiming: (n: number) => void; failedReaction: boolean; setFailedReaction: (b: boolean) => void;
}) {
  const [uid, setUid] = useState('');
  const [target, setTarget] = useState('');
  const [skillName, setSkillName] = useState('');
  const [skillValue, setSkillValue] = useState('');
  const [damage, setDamage] = useState('');
  const [count, setCount] = useState(1);
  const [pen, setPen] = useState('');
  const [leth, setLeth] = useState('');
  const [skill, setSkill] = useState(1);
  const [item, setItem] = useState(0);
  const [attribute, setAttribute] = useState('HP');
  const [value, setValue] = useState(10);
  const [serial, setSerial] = useState('Y1');
  const [team, setTeam] = useState('Y');
  const [main, setMain] = useState('s0');
  const [additional, setAdditional] = useState('none');
  const [reaction, setReaction] = useState('d');
  const [busy, setBusy] = useState(false);
  const units = room.snapshot.units;
  const selected = units.find(u => u.uid === uid) || units.find(u => u.controller === session) || units[0];
  const actor = units.find(u => u.uid === room.snapshot.turn?.uid);
  const host = room.host === session;
  const own = selected?.controller === session;
  const disabled = !live || busy || room.snapshot.state.phase === 'ended';
  async function utility(op: string, extra: Effect = {}) {
    setBusy(true);
    try { onRoom(await api<Room>(`/rooms/${room.code}/utility`, { op, uid: selected?.uid, ...extra })); }
    catch (e) { onError((e as Error).message); } finally { setBusy(false); }
  }
  async function attack() {
    setBusy(true);
    try {
      onRoom(await api<Room>(`/rooms/${room.code}/action`, { command: '.s0', target, count, bp_suffix: bonus,
        restim_mode: timing, allow_failed_reaction: failedReaction ? 1 : 0, actor: actor?.uid, revision: room.revision, request_id: requestId(),
        ...(skillName ? { skill_name: skillName } : {}), ...(skillValue ? { skill_value: Number(skillValue) } : {}),
        ...(damage ? { damage_dice: damage } : {}), ...(pen ? { penetration: Number(pen) } : {}), ...(leth ? { lethality: Number(leth) } : {}) }));
    } catch (e) { onError((e as Error).message); } finally { setBusy(false); }
  }
  return <details className="advanced-combat panel"><summary>mode2 操作与战斗管理</summary><p>下方设置作用于明确选中的单位；攻击操作属于当前行动角色。自动反应始终使用上方独立控件。</p>
    <div className="expansion-toolbar"><label>操作单位<select value={selected?.uid || ''} onChange={e => setUid(e.target.value)}>{units.map(u => <option key={u.uid} value={u.uid}>{u.team} 队 · {u.name}</option>)}</select></label>
      <label>目标<select value={target} onChange={e => setTarget(e.target.value)}><option value="">默认目标／自身</option><option value="R">随机单位 R</option><option value="RP">随机角色 RP</option>{units.map(u => <option key={u.uid} value={u.uid}>{u.team} 队 · {u.name}</option>)}</select></label>
      <label>攻击及反应奖惩骰<BonusSelect value={bonus} change={setBonus}/></label>
      <label>检定公布<select value={timing} onChange={e => setTiming(Number(e.target.value))}><option value={0}>立即公布</option><option value={1}>反应后一并公布</option></select></label>
      <label>攻击失败允许反应<input type="checkbox" checked={failedReaction} onChange={e => setFailedReaction(e.target.checked)}/></label>
    </div><h4>自定义攻击</h4><div className="expansion-toolbar"><label>技能名<input value={skillName} placeholder="使用默认技能" onChange={e => setSkillName(e.target.value)}/></label>
      <label>技能值<input type="number" value={skillValue} placeholder="默认" onChange={e => setSkillValue(e.target.value)}/></label><label>伤害骰<input value={damage} placeholder="使用角色伤害骰" onChange={e => setDamage(e.target.value)}/></label>
      <label>连续次数<input type="number" min="1" max="20" value={count} onChange={e => setCount(Number(e.target.value))}/></label>
      <label>贯穿<select value={pen} onChange={e => setPen(e.target.value)}><option value="">角色默认</option><option value="1">贯穿</option><option value="0">不贯穿</option></select></label>
      <label>致死骰<select value={leth} onChange={e => setLeth(e.target.value)}><option value="">角色默认</option><option value="1">启用</option><option value="0">禁用</option></select></label>
      <button className="button primary" disabled={disabled || actor?.controller !== session || !!room.snapshot.reaction} onClick={attack}>执行攻击</button></div>
    <h4>变身、持续效果与物品</h4><div className="expansion-toolbar"><button className="button" disabled={disabled || !own} onClick={() => utility('transform')}>变身</button><button className="button" disabled={disabled || !own} onClick={() => utility('untransform')}>解除变身</button>
      <label>技能编号<input type="number" min="1" max="30" value={skill} onChange={e => setSkill(Number(e.target.value))}/></label><button className="button" disabled={disabled || !own} onClick={() => utility('end_effect', { skill })}>结束持续效果</button>
      <label>物品<select value={item} onChange={e => setItem(Number(e.target.value))}><option value={-1}>请选择物品</option>{selected?.inventory.map((x, i) => <option key={i} value={i}>{x.item} ×{x.count}</option>)}</select></label>
      <button className="button" disabled={disabled || !own || !selected?.inventory.length} onClick={() => utility('use_item', { index: item, target })}>使用物品</button><button className="button" disabled={disabled || !own || !target || !selected?.inventory.length} onClick={() => utility('transfer_item', { index: item, target })}>转交物品</button></div>
    {host && <><h4>房主操作</h4><div className="expansion-toolbar"><button className="button" disabled={disabled} onClick={() => utility('save')}>保存战斗状态</button><button className="button" disabled={disabled} onClick={() => utility('restore')}>恢复战斗状态</button><button className="button" disabled={disabled} onClick={() => utility('reload')}>重载角色数据</button>
      <label>战斗属性<select value={attribute} onChange={e => setAttribute(e.target.value)}>{['HP', 'MP', 'SAN'].map(s => <option key={s}>{s}</option>)}</select></label><label>值<input type="number" value={value} onChange={e => setValue(Number(e.target.value))}/></label><button className="button" disabled={disabled} onClick={() => utility('attribute', { name: attribute, value })}>调整选中单位</button>
      <label>角色编号<input value={serial} onChange={e => setSerial(e.target.value.toUpperCase())}/></label><label>阵营<select value={team} onChange={e => setTeam(e.target.value)}><option>Y</option><option>X</option></select></label><button className="button" disabled={disabled} onClick={() => utility('add', { serial, team })}>加入战斗</button><button className="button" disabled={disabled} onClick={() => utility('remove', { serial })}>移出战斗</button><button className="button danger-text" disabled={disabled} onClick={() => utility('end')}>结束战斗</button>
    </div><h4>AI 固定策略</h4><p>使用现有策略语法，例如 s0、s3 Y1、none；反应使用 d／c／b。控制权可在房间面板交给 AI。</p><div className="expansion-toolbar"><label>主动作<input value={main} onChange={e => setMain(e.target.value)}/></label><label>附加动作<input value={additional} onChange={e => setAdditional(e.target.value)}/></label><label>反应<select value={reaction} onChange={e => setReaction(e.target.value)}><option value="d">闪避</option><option value="c">反击</option><option value="b">格挡</option></select></label><button className="button" disabled={disabled} onClick={() => utility('ai', { overrides: { main: [[[main, null]]], addi: [[[additional, null]]], react: [[[reaction, null]]] } })}>设置选中单位策略</button><button className="button" disabled={disabled} onClick={() => utility('ai', { overrides: {} })}>清除策略</button></div></>}
  </details>;
}
