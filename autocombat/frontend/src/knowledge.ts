import mechanics from '../../docs/游戏总机制.md?raw';
import type { Character } from './types';

export const mechanicsSections = mechanics.split(/^## /m).slice(1).map((section, i) => ({
  id: 'mechanics-' + (i + 1), title: section.split('\n')[0].replace(/^\d+\.\s*/, ''),
  content: section.slice(section.indexOf('\n')).trim(),
}));
export type Definition = { id: string; name: string; text: string; serial?: string; sections: string[] };
const common: [string, number][] = [
  ['主动',1],['附加动作',1],['被动',1],['受击反应',1],['先攻',1],['冷却',1],['阶段',1],
  ['MP',2],['HP',5],['SAN',2],['魔力',2],['理智',2],['准备阶段',2],['技能费用',2],
  ['D100',3],['奖励骰',3],['惩罚骰',3],['普通成功',3],['困难成功',3],['极难成功',3],['大成功',3],['贯穿',3],['伤害骰',3],
  ['闪避',4],['反击',4],['格挡',4],['多段攻击',4],['护盾',5],['吸血',5],['治疗',5],['回复',5],
  ['领域',6],['持续时间',6],['轮首',6],['叠加',6],['隔离',6],['db',6],['射程',6],['作用半径',6],
  ['召唤物',7],['召唤',7],['濒死',7],['死亡',7],['急救',7],['复活',7],['吟唱',8],['续唱',8],['蛋糕',8],['制造物',8],
];
// These names occur inside prose rather than as skill/trait titles.
const dedicated: Record<string, string[]> = {
  Y2:['使魔'],Y6:['冰冻','小雪人','雪带','雪球'], Y60:['雪带','雪球'],
  Y7:['战意','光环','沉默'], Y8:['生命蛋糕','魔力蛋糕','共享池','共享制造物池','品尝回复'],
  Y9:['生灵'],Y10:['藤蔓','蜂鸟','禁锢'],Y11:['致死骰'],Y12:['玄武','朱雀','龙虎','三合一'],
  Y14:['金环','极限值','极限槽','迅捷模式','勇气模式','魂斗罗','最终幻想','音速索尼克','卡带'],
  Y15:['灼烧'], Y16:['月能','灵感','影之克隆','克隆','月亮链接','月能馈赠','庇佑','伤害免疫','写作检定'],
  Y17:['血液','储血','人血','吟唱','续唱'],
};
const aliases:Record<string,Record<string,string>>={
  Y14:{'魂斗罗':'卡带切换·魂斗罗','最终幻想':'卡带切换·最终幻想','音速索尼克':'卡带切换·音速索尼克','凶斩':'极限技·凶斩'},
  Y16:{'即兴短句':'幻造兵武·即兴短句','乌托邦':'万众瞩目的乌托邦','理想城':'已然遥远的理想城','月光王座':'月光王座全额率输出','法杖':'切换·法杖','弓形态':'切换·弓','剑形态':'切换·剑'},
};
const clean = (text: string) => text.replace(/^#{1,6}\s*/gm, '').replace(/\*\*|`/g, '');
const attributeDefinitions: [string,string,number][] = [
  ['体力','生命资源，以 HP 表示；角色数值表分别列出变身前与变身后的满值。',5],
  ['生命','生命资源，以 HP 表示；伤害、治疗及濒死按相关规则结算。',5],
  ['行动力','影响角色的行动次数与移动能力；角色数值表不包含临时技能、被动和状态加成。',1],
  ['斗殴','普通近战攻击所用的技能值；特定武器或攻击可能使用其他技能值。',3],
  ['技能值','D100 检定使用的数值，决定普通、困难、极难等成功等级。',3],
  ['飞行','飞行角色的行动力获得飞行加成；具体角色的额外加减与限制见其技能。',1],
  ['普通攻击','使用角色或当前武器的普通攻击资料，消耗对应行动额度；普通攻击本身不新增 MP 费用。',2],
  ['攻击奖励骰','只作用于对应攻击检定的奖励骰，与惩罚骰抵消后计算净额。',3],
  ['攻击惩罚骰','只作用于对应攻击检定的惩罚骰，与奖励骰抵消后计算净额。',3],
];
export function buildDefinitions(catalog: Character[]): Definition[] {
  const definitions: Definition[] = common.map(([name, number]) => {
    const section = mechanicsSections[number - 1];
    const lines = section.content.split('\n').filter(line => line.includes(name));
    return { id: 'rule-' + name, name, text: clean((lines.length ? lines : [section.content]).join('\n')), sections:[section.id] };
  });
  definitions.push(...attributeDefinitions.map(([name,text,section])=>({id:'rule-'+name,name,text,sections:['mechanics-'+section]})));
  for (const c of catalog) {
    for (const t of c.traits) definitions.push({id:`${c.serial}-trait-${t.name}`,name:t.name,text:t.description,serial:c.serial,
      sections:definitions.find(d=>!d.serial&&d.name===t.name)?.sections || []});
    for (const name of new Set(c.skills.map(s=>s.name))) {
      const versions=c.skills.filter(s=>s.name===name);
      definitions.push({id:`${c.serial}-skill-${name}`,name,serial:c.serial,
        text:versions.map(s=>`${versions.length>1?'阶段 '+s.phase+' · ':''}${s.timing} · ${s.cost}\n${s.limits}\n${s.description}\n${s.facts.join('\n')}`).join('\n\n'),sections:['mechanics-1','mechanics-2']});
    }
    const prose = [...c.passives, c.basic, ...c.skills.flatMap(s => [s.description,...s.facts])];
    const headings = [...c.passives,...c.skills.flatMap(s=>s.facts)].filter(line => /^## /.test(line)).map(line => line.replace(/^## /,''));
    const quoted=[...prose.join('\n').matchAll(/[「《“]([^」》”\n]{2,24})[」》”]/g)].map(match=>match[1]);
    for (const name of new Set([...(dedicated[c.serial] || []), ...headings,...quoted])) {
      if(c.skills.some(s=>s.name===name))continue;
      const lines = prose.filter(line => line.includes(name) && !/^#/.test(line));
      for(const source of [c.passives,...c.skills.map(skill=>skill.facts)]) {
        const headingIndex=source.findIndex(line=>/^## /.test(line)&&line.includes(name));
        if(headingIndex>=0) {
          let end=headingIndex+1;while(end<source.length&&!/^## /.test(source[end]))end++;
          lines.push(...source.slice(headingIndex+1,end));
        }
      }
      if(name==='冰冻'&&c.serial==='Y6')lines.push(...c.passives);
      if (lines.length) definitions.push({id:`${c.serial}-term-${name}`,name,serial:c.serial,text:clean([...new Set(lines)].join('\n')),sections:(c.serial==='Y17'?[2,8]:c.serial==='Y8'?[8,5]:[6,7]).map(n=>'mechanics-'+n)});
    }
    for(const [name,target] of Object.entries(aliases[c.serial] || {})){
      const original=definitions.find(d=>d.serial===c.serial&&d.name===target);
      if(original)definitions.push({...original,id:`${c.serial}-alias-${name}`,name});
    }
  }
  return definitions;
}
export function termsFor(definitions: Definition[], serial?: string) {
  const terms = new Map<string,Definition>();
  for (const term of definitions.filter(d => !d.serial)) terms.set(term.name,term);
  for (const term of definitions.filter(d => d.serial === serial)) terms.set(term.name,term);
  return [...terms.values()].sort((a,b) => b.name.length-a.name.length);
}
export function splitTerms(text: string, terms: Definition[]): (string | Definition)[] {
  const parts:(string|Definition)[]=[]; let plain='';
  for (let i=0;i<text.length;) {
    const term=terms.find(t => text.startsWith(t.name,i) && (!/^[a-z0-9]+$/i.test(t.name) || !/[a-z0-9]/i.test(text[i-1]||'') && !/[a-z0-9]/i.test(text[i+t.name.length]||'')));
    if (term) { if(plain) parts.push(plain); plain=''; parts.push(term); i+=term.name.length; }
    else plain+=text[i++];
  }
  if(plain) parts.push(plain); return parts;
}
const summaries: Record<string,string> = {
  Y1:'以斗殴精通与火焰附魔强化近战，适合持续进攻。',Y2:'火球三段连击，配合使魔扩充战场人数；速攻提高行动力。',
  Y3:'以斗殴强化和附魔术提高近战输出。',Y4:'星闪双击输出，演唱干扰敌方行动。',Y5:'借助飞行机动，以吸血维持近战续航。',
  Y6:'雪带与雪球累积冰冻层数，触发八层循环中的易伤、减益、爆发和回复。',Y60:'以雪带领域与雪幕提供持续压制和减伤。',
  Y7:'削弱敌方战意，以治疗领域支援，并通过返生之花救回队友。',Y8:'制造恢复与增益蛋糕，分配个人库存和队伍共享补给。',
  Y9:'在生与灭之间转换阶段，配合生灵召唤组和领域作战。',Y10:'为队友提供护盾和治疗，以藤蔓保护自身、蜂鸟补充攻击。',
  Y11:'镰刀吸血维持续航，黑雾削弱闪避并抽取魔力。',Y12:'用空间效果强化友军、削弱敌人，从异界引入不同召唤物。',
  Y13:'飞行与魔力护体兼顾机动和生存，斗殴精通加强近战。',Y14:'切换三种卡带，分别使用射击、重剑与音速能力，管理极限槽和金环。',
  Y15:'连发火焰与领域制造爆发，火鼠裘消耗魔力抵御直接伤害。',Y16:'写作与幻造武器改变输出方式，以虚假之月储存月能、支援队伍。',
  Y17:'以储血塑造武装，兼顾防御、突击与吟唱；关注血液来源限制及续唱时机。',
};
export function overview(c: Character) {
  const hp=c.attrs['体力上限']??c.attrs['体力'],mp=c.attrs['魔力上限']??c.attrs['魔力'];
  return [c.role, ...(hp!=null&&mp!=null?[`基础满值 · HP ${hp} / MP ${mp}`]:[]),
    summaries[c.serial] || c.traits.map(t=>`${t.name}：${t.description}`).join('；') || '自定义角色，详情以当前技能资料为准。',
    ...c.skills.slice(0,3).map(s=>`${s.name} · ${s.cost}：${s.description.slice(0,44)}${s.description.length>44?'…':''}${s.limits?'（'+s.limits+'）':''}`)];
}
