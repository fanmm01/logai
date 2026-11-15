type Bounds = {top:number;bottom:number;left:number;right:number};
export function isOwnDefinition(reference:Element, definition:Element) {
  if(definition.contains(reference)||definition.closest('[data-definition-owner]')?.contains(reference))return true;
  // A named prose section owns its heading and body, not the other definitions
  // elsewhere in the character's passive rules.
  const heading=(node:Element)=>{
    const prose=node.closest('.encyclopedia-prose');if(!prose)return null;
    let block:Element|null=node;
    while(block&&block.parentElement!==prose)block=block.parentElement;
    while(block&&block.tagName!=='H5')block=block.previousElementSibling;
    return block;
  };
  const ownHeading=heading(definition);
  return !!ownHeading&&ownHeading===heading(reference);
}
export function definitionIsNearby(reference:Bounds, definition:Bounds, viewport:Bounds, interveningSkills:number) {
  const visible=(rect:Bounds)=>rect.bottom>rect.top&&rect.right>rect.left&&rect.top>=viewport.top-1&&rect.bottom<=viewport.bottom+1&&rect.left>=viewport.left-1&&rect.right<=viewport.right+1;
  return interveningSkills===0&&visible(reference)&&visible(definition);
}
export function interveningSkills(reference:Element, definition:Element, skills:Element[]) {
  const before=(a:Element,b:Element)=>!!(a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING);
  return skills.filter(skill=>!skill.contains(reference)&&!skill.contains(definition)&&
    (before(reference,skill)&&before(skill,definition)||before(definition,skill)&&before(skill,reference))).length;
}
