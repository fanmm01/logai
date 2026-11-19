const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const {loadTs}=require('./load-ts.cjs');
const {buildDefinitions,definitionHelpAnchor,termsFor,splitTerms,mechanicsSections,mechanicsDefinitions,specialistDefinitions,overview}=loadTs(path.resolve(__dirname,'../../frontend/src/knowledge.ts'));
const {KnowledgeProvider,DefinitionPanel,TermText,HelpPage}=loadTs(path.resolve(__dirname,'../../frontend/src/KnowledgeUI.tsx'));
const {pageFromPath,pageUrl,pages}=loadTs(path.resolve(__dirname,'../../frontend/src/navigation.ts'));
const profiles=require('../../frontend/src/data/encyclopedia.json');
const {catalog}=require('./catalog-fixture.cjs');
const definitions=buildDefinitions(catalog);
const {definitionIsNearby,isOwnDefinition}=loadTs(path.resolve(__dirname,'../../frontend/src/definitionVisibility.ts'));
test('self references are excluded by definition ownership independently of viewport and collapse',()=>{
  const reference={};
  assert.equal(isOwnDefinition(reference,{contains:node=>node===reference,closest:()=>null}),true);
  assert.equal(isOwnDefinition(reference,{contains:()=>false,closest:selector=>selector==='[data-definition-owner]'?{contains:node=>node===reference}:null}),true);
  assert.equal(isOwnDefinition(reference,{contains:()=>false,closest:selector=>selector==='[data-definition-owner]'?{contains:()=>false}:null}),false);
  assert.equal(isOwnDefinition(reference,{contains:()=>false,closest:()=>null}),false);
});
test('prose definitions own their heading and body, without swallowing neighboring definitions',()=>{
  const prose={};
  const node=(tagName,previousElementSibling=null)=>({tagName,parentElement:prose,previousElementSibling,contains:()=>false,
    closest:selector=>selector==='.encyclopedia-prose'?prose:null});
  const firstHeading=node('H5'),firstBody=node('P',firstHeading);
  const secondHeading=node('H5',firstBody),secondBody=node('P',secondHeading);
  assert.equal(isOwnDefinition(firstHeading,firstBody),true);
  assert.equal(isOwnDefinition(secondBody,firstBody),false);
});
test('local definitions require simultaneous visibility and no complete intervening skill',()=>{
  const viewport={top:50,bottom:850,left:20,right:1000};
  const reference={top:200,bottom:220,left:100,right:160};
  const definition={top:300,bottom:400,left:100,right:500};
  assert.equal(definitionIsNearby(reference,definition,viewport,0),true);
  assert.equal(definitionIsNearby(reference,definition,viewport,1),false);
  assert.equal(definitionIsNearby(reference,definition,viewport,2),false);
  assert.equal(definitionIsNearby(reference,{...definition,bottom:900},viewport,0),false);
  assert.equal(definitionIsNearby(reference,{...definition,top:0},viewport,0),false);
  assert.equal(definitionIsNearby(reference,{...definition,bottom:300},viewport,0),false);
  assert.equal(definitionIsNearby(reference,definition,{...viewport,right:300},0),false);
});

test('all UI paths resolve, including legacy root and invalid paths',()=>{
  for(const page of pages)assert.equal(pageFromPath(pageUrl(page)),page);
  for(const path of ['/ui/','/ui','/ui/missing'])assert.equal(pageFromPath(path),'home');
  assert.equal(pageUrl('battle','?room=ABC123'),'/ui/battle?room=ABC123');
});
test('mechanics use the actual eight document sections and all definition targets exist',()=>{
  assert.equal(mechanicsSections.length,8);
  assert.match(mechanicsSections[1].content,/普通闪避/);
  assert.match(mechanicsSections[1].content,/最终最低为 0 MP/);
  assert.doesNotMatch(mechanicsSections[1].content,/技能最低消耗.*1 MP|费用修正后仍至少为 1 MP/);
  assert.equal(new Set(definitions.map(d=>d.id)).size,definitions.length);
  for(const d of definitions){assert.ok(d.text,d.id);for(const section of d.sections)assert.ok(mechanicsSections.some(s=>s.id===section),d.id);}
});

test('card definitions preserve full worked rules and shared aliases instead of keyword-only sentences',()=>{
  const rule=name=>mechanicsDefinitions.find(d=>d.name===name).text;
  assert.match(rule('闪避'),/攻击极难 3、闪避困难 2/);
  assert.match(rule('闪避'),/原攻击为普通失败 -1 时加 2/);
  assert.match(rule('格挡'),/超出的伤害仍可扣 HP/);
  assert.match(rule('急救'),/治疗恰好为 3 时仍未脱离濒死/);
  assert.match(rule('主动作'),/0–8、9–13、14–18、19–23/);
  assert.match(rule('大失败'),/技能值不大于 0/);
  assert.match(rule('贯穿'),/不表示忽略护盾/);
  assert.match(rule('伤害上限'),/未统一应用/);
  assert.equal(rule('HP'),rule('体力'));
  assert.equal(rule('急救'),rule('濒死溢出伤害'));
  for(const section of mechanicsSections)for(const entry of section.entries)for(const name of entry.names){
    const definition=definitions.find(d=>d.id==='rule-'+name);
    assert.equal(definition.text,entry.content, name+' must keep every paragraph');
    assert.deepEqual(definition.sections,[section.id]);
  }
});

test('definition help links resolve aliases to rendered entries and preserve the selected section',()=>{
  const render=element=>renderToStaticMarkup(React.createElement(KnowledgeProvider,{catalog,serial:'Y2',openCharacter(){},openTerm(){}},element));
  const help=render(React.createElement(HelpPage,{tab:'mechanics',catalog,navigate(){}}));
  for(const definition of mechanicsDefinitions){
    assert.ok(help.includes(`id="${definitionHelpAnchor(definition)}"`),definition.name);
  }
  const magic=definitions.find(d=>d.id==='rule-魔力');
  assert.equal(definitionHelpAnchor(magic),'rule-MP');
  assert.equal(definitionHelpAnchor(magic,'mechanics-2'),'mechanics-2');
  const panel=render(React.createElement(DefinitionPanel,{id:magic.id,onClose(){},onOpen(){},onHelp(){}}));
  assert.match(panel,/href="\/ui\/help\/mechanics#rule-MP"/);
  assert.match(panel,/在帮助中查看/);
});

test('help deep links render exact local traits and skills even outside the default specialist index',()=>{
  for(const id of ['Y6-term-冰冻','Y9-trait-变身','Y2-skill-火球术']){
    const html=renderToStaticMarkup(React.createElement(HelpPage,{tab:'mechanics',catalog,navigate(){},anchor:encodeURIComponent(id)}));
    assert.ok(html.includes(`id="${id}"`),id);
  }
});

test('specialist index retains named resources and expands local traits without replacing character rules',()=>{
  const y14=specialistDefinitions(definitions,'Y14');
  assert.ok(y14.some(d=>d.name==='极限槽'));
  const blood=specialistDefinitions(definitions,'Y17');
  assert.match(blood.find(d=>d.name==='人血').text,/召唤物不提供血液/);
  const dodge=termsFor(definitions,'Y7').find(d=>d.name==='沉默');
  assert.match(dodge.text,/战意/);
  const flight=definitions.find(d=>d.serial==='Y5'&&d.name==='吸血');
  assert.match(flight.text,/MP/);
  const near=definitions.find(d=>d.id==='Y3-trait-近战');
  assert.match(near.text,/### 通用规则/);
  assert.match(near.text,/斜向相邻也为 1 格/);
  const form=definitions.find(d=>d.id==='Y9-trait-变身');
  assert.match(form.text,/阶段转换 · 专属规则/);
  assert.doesNotMatch(form.text,/### 通用规则/);
  assert.match(definitions.find(d=>d.id==='Y13-trait-魔躯').text,/魔力护体 · 专属规则/);
  assert.ok(specialistDefinitions(definitions,'Y17').some(d=>d.name==='血刃'));
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
test('external definitions link immediately while local terms await viewport measurement',()=>{
  const plain=renderToStaticMarkup(React.createElement(TermText,{text:'护盾'}));assert.equal(plain,'护盾');
  const linked=renderToStaticMarkup(React.createElement(KnowledgeProvider,{catalog,serial:'Y16',openCharacter(){},openTerm(){}},React.createElement(TermText,{text:'护盾与月能'})));
  assert.match(linked,/knowledge-link/);assert.match(linked,/term=rule-/);assert.ok(!/href="[^"]*Y16-term-/.test(linked));
  assert.equal((linked.match(/<a /g)||[]).length,1);
  assert.ok(linked.replace(/<[^>]*>/g,'').endsWith('与月能'));
});
test('local skills, traits, resources and aliases retain their complete identity before measurement',()=>{
  for(const c of catalog){
    for(const term of termsFor(definitions,c.serial).filter(d=>d.serial)){
      const html=renderToStaticMarkup(React.createElement(KnowledgeProvider,{catalog,serial:c.serial,openCharacter(){},openTerm(){}},React.createElement(TermText,{text:term.name})));
      assert.ok(!html.includes('<a '),`${c.serial}: ${term.name}`);
      assert.ok(!html.includes('preview-anchor'),`${c.serial}: ${term.name}`);
    }
  }
  const render=serial=>renderToStaticMarkup(React.createElement(KnowledgeProvider,{catalog,serial,openCharacter(){},openTerm(){}},React.createElement(TermText,{text:'吸血'})));
  assert.equal(render('Y5').replace(/<[^>]*>/g,''),'吸血');
  assert.match(render('Y2'),/knowledge-link/);
});
test('character help is only parallel character links; mechanics have stable section anchors',()=>{
  const render=tab=>renderToStaticMarkup(React.createElement(KnowledgeProvider,{catalog,openCharacter(){},openTerm(){}},React.createElement(HelpPage,{catalog,tab,navigate(){}})));
  const characters=render('characters');assert.equal((characters.match(/class="knowledge-link"/g)||[]).length,catalog.length);
  assert.ok(!characters.includes('character-card'));assert.ok(!characters.includes('preview-paragraph'));
  const mechanics=render('mechanics');for(const section of mechanicsSections)assert.ok(mechanics.includes(`id="${section.id}"`));
  assert.match(mechanics,/搜索术语或机制/);
  assert.match(mechanics,/id="mechanics-character-terms"/);
  assert.match(mechanics,/id="rule-闪避"/);
});
