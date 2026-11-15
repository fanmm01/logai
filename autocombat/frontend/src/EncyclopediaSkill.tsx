import type { Character } from './types';
import { EncyclopediaProse } from './EncyclopediaProse';
import { TermText } from './KnowledgeUI';

const parameterLabels = new Set(['目标', '作用对象', '射程', '作用半径', '范围', '持续时间', '技能冷却', '同时存在上限', '召唤数量']);

export function EncyclopediaSkill({ skill }: { skill: Character['skills'][number] }) {
  const parameters = [{ label: '消耗', value: skill.cost || '1 MP' }];
  const facts: string[] = [];
  let inTemplate = false;
  for (const line of skill.facts) {
    if (/^#{1,6}\s|^小雪人$/.test(line)) inTemplate = true;
    const match = line.match(/^([^：]+)：(.+)$/);
    if (!inTemplate && match && parameterLabels.has(match[1])) {
      parameters.push({ label: match[1], value: match[2].trim() });
    } else {
      facts.push(line);
    }
  }
  if (skill.limits) parameters.push({ label: '范围与条件', value: skill.limits });
  return <>
    <EncyclopediaProse content={skill.description} sourceKey={'skill:'+skill.id}/>
    <dl className="skill-meta">{parameters.map(({ label, value }, i) =>
      <div key={i}><dt><TermText text={label}/></dt><dd><TermText text={value}/></dd></div>)}</dl>
    {facts.length > 0 && <div className="skill-facts"><EncyclopediaProse content={facts}/></div>}
  </>;
}
