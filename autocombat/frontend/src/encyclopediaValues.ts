import type { Character, Effect } from './types';

export type EncyclopediaData = { character: Character; spell: Effect; summonTemplate?: Effect };

function readPath(value: unknown, parts: string[]): unknown {
  if (!parts.length) return value;
  const [key, ...tail] = parts;
  if (key === '*' && Array.isArray(value)) {
    const values = value.map(item => readPath(item, tail));
    return values.some(item => item == null) ? undefined : values;
  }
  if (value == null || typeof value !== 'object' || !Object.hasOwn(value, key)) return undefined;
  return readPath((value as Record<string, unknown>)[key], tail);
}

function namedSkills(skills: unknown): Record<string, Effect> {
  if (!Array.isArray(skills)) return {};
  return Object.fromEntries(skills.flatMap(skill => {
    if (typeof skill === 'object' && skill?.name) return [[skill.name, skill]];
    if (typeof skill !== 'string') return [];
    const match = skill.match(/^([^:]+):\s*(\d+)\s+(.+)$/);
    return match ? [[match[1], { name: match[1], val: Number(match[2]), dice: match[3] }]] : [];
  }));
}

function format(value: unknown, filter: string, roots: Record<string, unknown>): unknown {
  const [name, argument = ''] = filter.split(':');
  if (Array.isArray(value) && !['count', 'unique', 'join'].includes(name)) return value.map(item => format(item, filter, roots));
  const number = Number(value);
  switch (name) {
    case 'abs': return Math.abs(number);
    case 'percent': return Number((number * 100).toFixed(6));
    case 'signed': return number > 0 ? `+${number}` : String(number);
    case 'enabled': return number ? '开启' : '关闭';
    case 'unsigned': return String(value).replace(/^\+/, '');
    case 'signedDice': return String(value).startsWith('-') ? String(value).replace(/^-/, '−') : `+${value}`;
    case 'suffix': return String(value) + argument;
    case 'formula': return ({ summon_count_ceil_half: '实际召唤数量的一半，向上取整，消耗等量 MP', ceil_2d6_div_2: '掷 2d6，结果除以 2 向上取整，消耗等量 MP' } as Record<string, string>)[String(value)];
    case 'bonus': { const match = String(value).match(/^(?:(\d+)?[bp]|[bp](\d+)?)$/i); return match ? Number(match[1] || match[2] || 1) : Math.abs(number); }
    case 'modifier': {
      const escaped = argument.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const match = String(value).match(new RegExp(`${escaped}\\s*([+-]\\d+(?:\\.\\d+)?)`));
      return match ? Number(match[1]) : undefined;
    }
    case 'count': return Array.isArray(value) ? value.length : undefined;
    case 'unique': return Array.isArray(value) ? [...new Set(value)] : undefined;
    case 'join': return Array.isArray(value) ? value.join(argument ?? '、') : undefined;
    case 'cn': return ['零', '一', '两', '三', '四', '五', '六', '七', '八', '九', '十'][number] ?? String(value);
    case 'fraction': {
      for (let denominator = 1; denominator <= 100; denominator++) {
        const numerator = Math.round(number * denominator);
        if (Math.abs(number - numerator / denominator) < 1e-8) return `${numerator}/${denominator}`;
      }
      return Number.isFinite(number) ? String(number) : undefined;
    }
    case 'minus': return number - Number(argument);
    case 'plus': return number + Number(argument);
    case 'times': return number * Number(argument);
    case 'add': return number + Number(readPath(roots, argument.split('.')));
    case 'multiply': return number * Number(readPath(roots, argument.split('.')));
    case 'divide': return number / Number(readPath(roots, argument.split('.')));
    case 'ceil': return Math.ceil(number);
    case 'floor': return Math.floor(number);
    case 'mpAfterReduction': return Math.max(0, Math.floor(number * (1 + Number(readPath(roots, argument.split('.'))) / 100)));
    default: return undefined;
  }
}

// References are explicit and scoped. Never infer a value from surrounding prose.
// Formatting is allowlisted; no expressions or code from articles are executed.
export function resolveEncyclopediaValues(text: string, data: EncyclopediaData): string {
  const summons = data.character.summon_templates || {};
  const spellList = data.character.spells || [];
  const roots: Record<string, unknown> = {
    ...data,
    summons, items: data.character.item_templates || {},
    skills: Object.fromEntries(spellList.flatMap(spell => [[spell.name, spell], [`${spell.name}@${spell.phase ?? 0}`, spell]])),
    summonSkills: Object.fromEntries(Object.entries(summons).map(([name, template]) => [name, namedSkills(template.skills)])),
    summonSpells: Object.fromEntries(Object.entries(summons).map(([name, template]) => [name, namedSkills(template.spells)])),
  };
  return text.replace(/\{\{([^{}]+)\}\}/g, (token, reference: string) => {
    const [path, ...filters] = reference.split('|').map(part => part.trim());
    if (!Object.hasOwn(roots, path.split('.')[0])) return token;
    let value = readPath(roots, path.split('.'));
    for (const filter of filters) {
      if (value == null) break;
      value = format(value, filter, roots);
    }
    if (value == null || typeof value === 'object' || typeof value === 'number' && !Number.isFinite(value)) return '未配置';
    return String(value);
  });
}
