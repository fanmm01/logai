import articles from './data/encyclopedia.json';
import type { Character, Effect } from './types';

export type CatalogCharacter = Pick<Character, 'serial' | 'name' | 'attrs' | 'str_attrs' | 'spells' | 'portrait' | 'avatar'>;
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
  const skills = character.spells.map(spell => profile?.skills.find(article =>
    article.name === spell.name && (article.phase ?? 0) === (spell.phase ?? 0)) ?? missingArticle(spell));
  return {
    ...character,
    emblem: profile?.emblem ?? 'star', color: profile?.color ?? '#847fc0',
    role: profile?.role ?? '自定义角色', traits: profile?.traits ?? [],
    passives: profile?.passives ?? [], basic: profile?.basic ?? '', skills,
  };
}
