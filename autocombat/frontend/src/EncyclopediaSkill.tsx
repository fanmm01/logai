import type { Character } from './types';
import { EncyclopediaProse } from './EncyclopediaProse';
import { TermText } from './KnowledgeUI';
import { resolveEncyclopediaValues, type EncyclopediaData } from './encyclopediaValues';

const parameterLabels = new Set(['目标', '作用对象', '射程', '作用半径', '范围', '持续时间', '技能冷却', '同时存在上限', '召唤数量']);

export function EncyclopediaSkill({ skill, data }: { skill: Character['skills'][number]; data: EncyclopediaData }) {
  const summonName = (data.spell.effects || []).map((effect: Record<string, unknown>) => effect['召唤物模板']).find(Boolean);
  const resolvedData = { ...data, summonTemplate: summonName ? data.character.summon_templates?.[String(summonName)] : undefined };
  const parameters = [{ label: '消耗', value: resolveEncyclopediaValues(skill.cost || '0 MP', resolvedData) }];
  const facts: string[] = [];
  let inTemplate = false;
  for (const line of skill.facts) {
    if (/^#{1,6}\s|^小雪人$/.test(line)) inTemplate = true;
    const match = line.match(/^([^：]+)：(.+)$/);
    if (!inTemplate && match && parameterLabels.has(match[1])) {
      parameters.push({ label: match[1], value: resolveEncyclopediaValues(match[2].trim(), resolvedData) });
    } else {
      facts.push(line);
    }
  }
  if (skill.limits) parameters.push({ label: '范围与条件', value: resolveEncyclopediaValues(skill.limits, resolvedData) });
  return <>
    <EncyclopediaProse content={skill.description} sourceKey={'skill:'+skill.id} data={resolvedData}/>
    <dl className="skill-meta">{parameters.map(({ label, value }, i) =>
      <div key={i}><dt><TermText text={label}/></dt><dd><TermText text={value}/></dd></div>)}</dl>
    {facts.length > 0 && <div className="skill-facts"><EncyclopediaProse content={facts} data={resolvedData}/></div>}
  </>;
}
