import articles from './data/encyclopedia.json';
import type { Character, Effect } from './types';
import { resolveEncyclopediaValues } from './encyclopediaValues';

export type CatalogCharacter = Pick<Character, 'serial' | 'name' | 'attrs' | 'str_attrs' | 'spells' | 'portrait' | 'avatar' | 'summon_templates' | 'item_templates' | 'passive_rules' | 'blood_rules' | 'inventory' | 'initial_phase' | 'initial_weapon' | '初始血液'>;
type Profile = Pick<Character, 'emblem' | 'color' | 'role' | 'traits' | 'skills' | 'passives' | 'basic'>;
const profiles: Record<string, Profile> = articles;
const timingLabels: Record<string, string> = { '1': '被动', '2': '主动', '3': '附加动作', '4': '受击反应' };

function missingArticle(spell: Effect): Character['skills'][number] {
  return {
    id: JSON.stringify([spell.name, spell.phase ?? 0]),
    name: spell.name || '未命名技能', phase: spell.phase ?? 0,
    timing: Array.from(String(spell.timing ?? '2'), value => timingLabels[value] ?? value).join(' / '),
    cost: '尚未编写', limits: '', description: '此技能的前端百科条目尚未编写。', facts: [], icon: '/ui/icons/star.svg',
  };
}

export function withEncyclopedia(character: CatalogCharacter): Character {
  const profile = profiles[character.serial];
  // Match by name and phase so editing skill order cannot attach the wrong prose.
  const result: Character = {
    ...character,
    emblem: profile?.emblem ?? 'star', color: profile?.color ?? '#847fc0',
    role: profile?.role ?? '自定义角色', traits: profile?.traits ?? [],
    passives: [], basic: '', skills: [],
  };
  const resolve = (text: string, spell: Effect = {}) => resolveEncyclopediaValues(text, { character: result, spell });
  result.traits = result.traits.map(trait => ({ ...trait, description: resolve(trait.description) }));
  result.passives = (profile?.passives ?? []).map(text => resolve(text));
  result.basic = resolve(profile?.basic ?? '');
  result.skills = character.spells.map(spell => {
    const article = profile?.skills.find(article => article.name === spell.name && (article.phase ?? 0) === (spell.phase ?? 0)) ?? missingArticle(spell);
    return { ...article, cost: resolve(article.cost, spell), limits: resolve(article.limits, spell),
      description: resolve(article.description, spell), facts: article.facts.map(line => resolve(line, spell)),
      timing: spell.timing == null ? article.timing : Array.from(String(spell.timing), value => timingLabels[value] ?? value).join('／') };
  });
  return result;
}
