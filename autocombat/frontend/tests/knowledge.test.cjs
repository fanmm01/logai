const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const {loadTs}=require('./load-ts.cjs');
const {buildDefinitions,termsFor,splitTerms,mechanicsSections,overview}=loadTs(path.resolve(__dirname,'../src/knowledge.ts'));
const {KnowledgeProvider,TermText,HelpPage}=loadTs(path.resolve(__dirname,'../src/KnowledgeUI.tsx'));
const {pageFromPath,pageUrl,pages}=loadTs(path.resolve(__dirname,'../src/navigation.ts'));
const profiles=require('../src/data/encyclopedia.json');
const catalog=Object.entries(profiles).map(([serial,profile])=>({...profile,serial,name:serial,attrs:{},spells:[],portrait:'',avatar:''}));
const definitions=buildDefinitions(catalog);

test('all UI paths resolve, including legacy root and invalid paths',()=>{
  for(const page of pages)assert.equal(pageFromPath(pageUrl(page)),page);
  for(const path of ['/ui/','/ui','/ui/missing'])assert.equal(pageFromPath(path),'home');
  assert.equal(pageUrl('battle','?room=ABC123'),'/ui/battle?room=ABC123');
});
test('mechanics use the actual eight document sections and all definition targets exist',()=>{
  assert.equal(mechanicsSections.length,8);
  assert.match(mechanicsSections[1].content,/普通闪避/);
  assert.match(mechanicsSections[1].content,/1 MP/);
  assert.equal(new Set(definitions.map(d=>d.id)).size,definitions.length);
  for(const d of definitions){assert.ok(d.text,d.id);for(const section of d.sections)assert.ok(mechanicsSections.some(s=>s.id===section),d.id);}
});
test('all skill and trait names resolve inside their character, with both phases preserved',()=>{
  for(const c of catalog){
    const terms=termsFor(definitions,c.serial);
    for(const item of [...c.skills,...c.traits])assert.ok(terms.some(t=>t.name===item.name),c.serial+item.name);
    assert.ok(overview(c).length>=2);
  }
  const summon=termsFor(definitions,'Y9').find(d=>d.name==='召唤生灵');
  assert.match(summon.text,/阶段 1/);assert.match(summon.text,/阶段 2/);
  assert.match(termsFor(definitions,'Y5').find(d=>d.name==='吸血').text,/MP/);
  assert.match(termsFor(definitions,'Y6').find(d=>d.name==='八层循环').text,/第 8 层/);
});
test('longest complete terms win; Latin abbreviations do not split ordinary words',()=>{
  const terms=termsFor(definitions,'Y16');
  const parts=splitTerms('月亮链接使 MP 恢复；SAMPLE HP',terms);
  assert.equal(parts[0].name,'月亮链接');
  assert.ok(parts.some(p=>p.name==='MP'));assert.ok(parts.some(p=>p.name==='HP'));
  assert.ok(parts.some(p=>typeof p==='string'&&p.includes('SAMPLE')));
});
test('terms are plain text outside a character and blue definition links inside it',()=>{
  const plain=renderToStaticMarkup(React.createElement(TermText,{text:'护盾'}));assert.equal(plain,'护盾');
  const linked=renderToStaticMarkup(React.createElement(KnowledgeProvider,{catalog,serial:'Y16',openCharacter(){},openTerm(){}},React.createElement(TermText,{text:'护盾与月能'})));
  assert.match(linked,/knowledge-link/);assert.match(linked,/term=rule-/);assert.match(linked,/Y16-term-/);
  assert.equal((linked.match(/<a /g)||[]).length,2);
});
test('character help is only parallel character links; mechanics have stable section anchors',()=>{
  const render=tab=>renderToStaticMarkup(React.createElement(KnowledgeProvider,{catalog,openCharacter(){},openTerm(){}},React.createElement(HelpPage,{catalog,tab,navigate(){}})));
  const characters=render('characters');assert.equal((characters.match(/class="knowledge-link"/g)||[]).length,catalog.length);
  assert.ok(!characters.includes('character-card'));assert.ok(!characters.includes('preview-paragraph'));
  const mechanics=render('mechanics');for(const section of mechanicsSections)assert.ok(mechanics.includes(`id="${section.id}"`));
});
