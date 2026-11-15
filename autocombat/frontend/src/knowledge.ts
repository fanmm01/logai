import mechanics from '../../docs/游戏总机制.md?raw';
import type { Character } from './types';

// A complete Markdown entry is the shared source for help and card definitions.
// Section numbers and rule-name IDs remain compatible with saved glossary URLs.
export const mechanicsSections = mechanics.split(/^## /m).slice(1).map((section, i) => {
  const content = section.slice(section.indexOf('\n')).trim();
  return {
    id: 'mechanics-' + (i + 1), title: section.split('\n')[0].trim().replace(/^\d+\.\s*/, ''), content,
    entries: content.split(/^### /m).slice(1).map(entry => ({
      names: entry.slice(0, entry.indexOf('\n')).trim().split(' / '),
      content: entry.slice(entry.indexOf('\n')).trim(),
    })),
  };
});
export type Definition = { id: string; name: string; text: string; serial?: string; sections: string[]; sources?:string[] };
export const mechanicsDefinitions: Definition[] = mechanicsSections.flatMap(section => section.entries.flatMap(entry =>
  entry.names.map(name => ({id:'rule-'+name, name, text:entry.content, sections:[section.id]}))));
export function definitionHelpAnchor(definition:Definition,section?:string|null) {
  if(section)return section;
  if(definition.serial)return definition.id;
  const entry=mechanicsSections.flatMap(s=>s.entries).find(e=>e.names.includes(definition.name));
  return entry?'rule-'+entry.names[0]:definition.sections[0]||'mechanics-character-terms';
}
// These names occur inside prose rather than as skill/trait titles.
const dedicated: Record<string, string[]> = {
  Y2:['使魔'],Y6:['冰冻','小雪人','雪带','雪球'], Y60:['雪带','雪球'],
  Y7:['战意','光环','沉默'], Y8:['生命蛋糕','魔力蛋糕','共享池','共享制造物池','品尝回复'],
  Y9:['生灵'],Y10:['藤蔓','蜂鸟','禁锢'],Y11:['致死骰'],Y12:['玄武','朱雀','龙虎','三合一'],
  Y14:['金环','极限值','极限槽','迅捷模式','勇气模式','魂斗罗','最终幻想','音速索尼克','卡带'],
  Y15:['灼烧'], Y16:['月能','灵感','影之克隆','克隆','月亮链接','月能馈赠','庇佑','伤害免疫','写作检定'],
  Y17:['血液','储血','人血','血刃','血弓','长枪','吟唱','续唱'],
};
// Trait labels are editorial summaries; bind their full explanation to actual
// abilities instead of guessing a mechanic from a short label such as “守护”.
const traitSkills:Record<string,Record<string,string[]>>={
  Y1:{'附魔':['火焰附魔'],'强攻':['斗殴精通']},
  Y2:{'连击':['火球术'],'召唤':['召唤使魔']},Y3:{'强化':['斗殴强化','附魔术']},
  Y4:{'双击':['星闪攻击'],'干扰':['演唱']},Y5:{'吸血':['吸血'],'机动':['飞行']},
  Y6:{'守护':['雪幕','雪之法杖']},Y60:{'领域':['制造雪带'],'减伤':['雪幕']},
  Y7:{'战意':['战意值削弱'],'救援':['治愈领域','返生之花']},
  Y8:{'制造':['美味的蛋糕','甜甜的蛋糕','轻盈蛋糕','幸运蛋糕'],'补给':['品尝蛋糕','最后的款待-恢复','最后的款待-进攻']},
  Y9:{'变身':['阶段转换'],'生灭':['领域·生','领域·灭','召唤生灵']},
  Y10:{'守护':['屏障术','治疗术'],'召唤':['藤蔓术','召唤蜂鸟']},
  Y11:{'汲取':['挥动镰刀','镰刃余势'],'黑雾':['黑雾笼罩']},
  Y12:{'空间':['空间扭曲','空间压缩','空间扩张'],'引入':['空间引入']},
  Y13:{'飞行':['飞行'],'魔躯':['魔力永驻','魔力护体']},
  Y14:{'卡带':['卡带切换·魂斗罗','卡带切换·最终幻想','卡带切换·音速索尼克'],'蓄势':['极限槽','金环']},
  Y15:{'爆破':['爆破圈','瞬爆','余烬','葬火流光'],'火衣':['火鼠裘']},
  Y16:{'幻造':['幻造兵武','切换·法杖','切换·弓','切换·剑'],'月能':['虚假之月']},
  Y17:{'血术':['猩红眼眸','阿瑞卡纳律动','等价交换'],'塑兵':['凝血式-幻','凝血式-存','血色突刺']},
};
const aliases:Record<string,Record<string,string>>={
  Y14:{'魂斗罗':'卡带切换·魂斗罗','最终幻想':'卡带切换·最终幻想','音速索尼克':'卡带切换·音速索尼克','凶斩':'极限技·凶斩'},
  Y16:{'即兴短句':'幻造兵武·即兴短句','乌托邦':'万众瞩目的乌托邦','理想城':'已然遥远的理想城','月光王座':'月光王座全额率输出','法杖':'切换·法杖','弓形态':'切换·弓','剑形态':'切换·剑'},
  Y17:{'人血':'储血'},
};
const dedicatedSections:Record<string,number[]>={
  Y2:[7],Y6:[5,6,7],Y60:[5,6],Y7:[1,6,7],Y8:[5,8],Y9:[1,7],Y10:[4,5,6,7],
  Y11:[3,5,6],Y12:[4,6,7],Y14:[1,2,6],Y15:[5,6],Y16:[2,3,5,6,7],Y17:[2,5,8],
};
const clean = (text: string) => text.replace(/^#{1,6}\s*/gm, '').replace(/\*\*|`/g, '');
export function buildDefinitions(catalog: Character[]): Definition[] {
  const definitions: Definition[] = [...mechanicsDefinitions];
  for (const c of catalog) {
    for (const t of c.traits) {
      const common = c.serial==='Y9'&&t.name==='变身'?undefined:mechanicsDefinitions.find(d=>d.name===t.name);
      definitions.push({id:`${c.serial}-trait-${t.name}`,name:t.name,
        text:t.description+(common?'\n\n### 通用规则\n\n'+common.text:''),serial:c.serial,
        sections:common?.sections || [],sources:['trait:'+t.name]});
    }
    for (const name of new Set(c.skills.map(s=>s.name))) {
      const versions=c.skills.filter(s=>s.name===name);
      definitions.push({id:`${c.serial}-skill-${name}`,name,serial:c.serial,
        text:versions.map(s=>`${versions.length>1?'阶段 '+s.phase+' · ':''}${s.timing} · ${s.cost}\n${s.limits}\n${s.description}\n${s.facts.join('\n')}`).join('\n\n'),sections:['mechanics-1','mechanics-2'],sources:versions.map(s=>'skill:'+s.id)});
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
      if (lines.length) definitions.push({id:`${c.serial}-term-${name}`,name,serial:c.serial,text:clean([...new Set(lines)].join('\n')),
        sections:[...new Set([...(mechanicsDefinitions.find(d=>d.name===name)?.sections||[]),...(dedicatedSections[c.serial]||[6,7]).map(n=>'mechanics-'+n)])],sources:['prose:'+lines[0]]});
    }
    for(const [name,target] of Object.entries(aliases[c.serial] || {})){
      const original=definitions.find(d=>d.serial===c.serial&&d.name===target);
      if(original)definitions.push({...original,id:`${c.serial}-alias-${name}`,name});
    }
    // Keep saved trait URLs as detailed as their bound abilities and resources.
    for(const trait of definitions.filter(d=>d.serial===c.serial&&d.id.startsWith(c.serial+'-trait-'))) {
      const local=definitions.find(d=>d.serial===c.serial&&d.name===trait.name&&d.id.startsWith(c.serial+'-term-'))
        ||definitions.find(d=>d.serial===c.serial&&d.name===trait.name&&d.id.startsWith(c.serial+'-skill-'));
      const abilities=(traitSkills[c.serial]?.[trait.name]||[]).map(name=>definitions.find(d=>d.serial===c.serial&&d.name===name&&d.id.startsWith(c.serial+'-skill-'))).filter((d):d is Definition=>!!d);
      for(const source of [...(local?[local]:[]),...abilities])if(!trait.text.includes(source.text)) {
        trait.text+='\n\n### '+source.name+' · 专属规则\n\n'+source.text;
        trait.sections=[...new Set([...trait.sections,...source.sections])];
      }
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
export function specialistDefinitions(definitions:Definition[],serial:string) {
  return termsFor(definitions,serial).filter(d=>d.serial&&(!d.id.startsWith(serial+'-skill-')||dedicated[serial]?.includes(d.name)));
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
