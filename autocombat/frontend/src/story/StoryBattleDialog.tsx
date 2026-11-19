import type {ReactNode} from 'react';
import type {Character} from '../types';
import type {StoryRun} from './types';
import {Swords,ArrowRight,Heart} from 'lucide-react';
export function StoryBattleDialog({run,catalog,busy,onStart,renderCharacter}:{run:StoryRun;catalog:Character[];busy:boolean;onStart:()=>void;renderCharacter:(c:Character)=>ReactNode}) {
  const encounter=run.encounter;if(!encounter)return null;
  const battle=encounter.battles[encounter.wave];
  return <section className="story-encounter panel" role="dialog" aria-modal="true" aria-label="确认阵容"><div className="story-section-title"><Swords/><div><small>STORY ENCOUNTER</small><h2>确认阵容</h2></div><span>{encounter.battles.length} 波挑战</span></div><div className="story-encounter-teams">{[battle.a,battle.b].map(team=><section key={team}><h3>{team===encounter.bind?'你的伙伴':'对方阵容'} · {team}</h3><div className="story-encounter-cards">{encounter.teams[team].map(serial=>{const c=catalog.find(x=>x.serial===serial);return c?<div key={serial}>{renderCharacter(c)}<div className="story-skill-list">{c.skills.map(s=><span key={s.id} title={s.description}>{s.name}</span>)}</div></div>:<p key={serial}>{serial}</p>;})}</div></section>)}</div><div className="story-waves">{encounter.battles.map((b,i)=><span key={i}>{i+1}. {b.a} vs {b.b}{b.keep&&<><Heart size={12}/> 继承至下一波</>}</span>)}</div><div className="story-actions"><p>由 AI 自动作战，可在战术室暂停、单步和调整速度。</p><button className="button primary" disabled={busy} onClick={onStart}>开始战斗 <ArrowRight size={16}/></button></div></section>;
}
