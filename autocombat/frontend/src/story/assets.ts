/** Insert a resource at the caret using the same author-facing DSL as the parser. */
export function referenceForAsset(path:string,flow=false):string {
  const [folder,...parts]=path.split('/');const name=parts.join('/');
  if(folder==='background'&&name)return '\\bg '+name;
  if(folder==='bgm'&&name)return '\\m '+name;
  if(folder==='figure'&&name)return 'changeFigure:'+name+';';
  if(folder==='vocal'&&name)return '\\se '+name;
  if(/\.(mp4|webm|ogg)$/i.test(path)){const video=folder==='video'?name:path;return flow?'->'+video:'\\video '+video;}
  throw new Error('该资源类型请使用原生命令引用');
}
