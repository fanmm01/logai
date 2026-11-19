import {useEffect,useRef} from 'react';
import {EditorState} from '@codemirror/state';
import {EditorView,keymap,lineNumbers,highlightActiveLine,gutter} from '@codemirror/view';
import {defaultKeymap,history,historyKeymap} from '@codemirror/commands';
import {autocompletion} from '@codemirror/autocomplete';
import {setDiagnostics} from '@codemirror/lint';
export function SourceEditor({value,onChange,onSave,line,diagnostics=[],insertion}:{value:string;onChange:(text:string)=>void;onSave:()=>void;line?:number;diagnostics?:{line?:number;column?:number;message:string}[];insertion?:{id:number;text:string}}) {
  const root=useRef<HTMLDivElement>(null),view=useRef<EditorView|null>(null),callbacks=useRef({onChange,onSave});callbacks.current={onChange,onSave};
  useEffect(()=>{
    if(!root.current)return;
    const editor=new EditorView({parent:root.current,state:EditorState.create({doc:value,extensions:[autocompletion({override:[context=>{const word=context.matchBefore(/[\\\w@_-]+/);if(!word&&!context.explicit)return null;return {from:word?.from??context.pos,options:["\\mem ","\\bg ","\\m ","\\b","__LOCK ","UNLOCK@","LOCK@","@START:","KEEP_STATUS","CONTINUE-"].map(label=>({label,type:"keyword"}))};}]}),lineNumbers(),highlightActiveLine(),history(),EditorView.lineWrapping,keymap.of([{key:'Mod-s',run:()=>{callbacks.current.onSave();return true;}},...defaultKeymap,...historyKeymap]),EditorView.updateListener.of(update=>{if(update.docChanged)callbacks.current.onChange(update.state.doc.toString());}),EditorView.theme({'&':{height:'100%',fontSize:'14px'},'.cm-scroller':{overflow:'auto',fontFamily:'Consolas, monospace'},'.cm-content':{padding:'16px'},'.cm-gutters':{background:'#f4f1f8',color:'#a098ad',border:'none'}})]})});view.current=editor;
    return()=>{editor.destroy();view.current=null;};
  },[]);
  useEffect(()=>{const editor=view.current;if(!editor||!insertion)return;let from=editor.state.selection.main.from,to=editor.state.selection.main.to;
    if(from===0&&editor.state.doc.line(1).text.startsWith('@'))from=to=Math.min(editor.state.doc.length,editor.state.doc.line(1).to+1);
    const text=(from>0&&editor.state.sliceDoc(from-1,from)!=='\n'?'\n':'')+insertion.text+'\n';editor.dispatch({changes:{from,to,insert:text},selection:{anchor:from+text.length}});editor.focus();
  },[insertion?.id]);
  useEffect(()=>{const editor=view.current;if(!editor)return;editor.dispatch(setDiagnostics(editor.state,diagnostics.map(d=>{const l=editor.state.doc.line(Math.min(Math.max(1,d.line||1),editor.state.doc.lines));const from=Math.min(l.to,l.from+(d.column||1)-1);return {from,to:Math.max(from,Math.min(l.to,from+1)),severity:'error' as const,message:d.message};})));},[diagnostics,value]);
  useEffect(()=>{const editor=view.current;if(editor&&editor.state.doc.toString()!==value)editor.dispatch({changes:{from:0,to:editor.state.doc.length,insert:value}});},[value]);
  useEffect(()=>{const editor=view.current;if(editor&&line){const target=editor.state.doc.line(Math.min(Math.max(1,line),editor.state.doc.lines));editor.dispatch({selection:{anchor:target.from},effects:EditorView.scrollIntoView(target.from,{y:'center'})});editor.focus();}},[line]);
  return <div className="story-source-editor" ref={root} aria-label="剧情源码编辑器"/>;
}
