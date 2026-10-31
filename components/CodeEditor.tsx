'use client';
import {useEffect,useRef} from 'react';
import {basicSetup} from 'codemirror';
import {EditorState} from '@codemirror/state';
import {EditorView,keymap} from '@codemirror/view';
import {indentWithTab} from '@codemirror/commands';
import {HighlightStyle,syntaxHighlighting} from '@codemirror/language';
import {tags} from '@lezer/highlight';
import {python} from '@codemirror/lang-python';

const monochrome=HighlightStyle.define([
  {tag:[tags.keyword,tags.operator,tags.definitionKeyword],color:'#f0f0f0'},
  {tag:[tags.string,tags.number,tags.bool],color:'#b3b3b3'},
  {tag:[tags.comment],color:'#626262',fontStyle:'italic'},
  {tag:[tags.variableName,tags.function(tags.variableName)],color:'#d4d4d4'},
]);
export default function CodeEditor({value,onChange,onRun}:{value:string;onChange:(value:string)=>void;onRun:()=>void}){
  const host=useRef<HTMLDivElement>(null),view=useRef<EditorView|null>(null);
  const change=useRef(onChange),run=useRef(onRun);
  change.current=onChange;run.current=onRun;
  useEffect(()=>{
    if(!host.current)return;
    const editor=new EditorView({parent:host.current,state:EditorState.create({doc:value,extensions:[
      basicSetup,python(),syntaxHighlighting(monochrome),keymap.of([indentWithTab,{key:'Mod-Enter',run:()=>{run.current();return true;}}]),
      EditorView.contentAttributes.of({'aria-label':'Python strategy code',role:'textbox','aria-multiline':'true',spellcheck:'false'}),
      EditorView.updateListener.of(update=>{if(update.docChanged)change.current(update.state.doc.toString());}),
      EditorView.theme({
        '&':{height:'530px',backgroundColor:'#080808',color:'#ddd',fontSize:'12px'},
        '.cm-scroller':{fontFamily:'Consolas, monospace',lineHeight:'1.9',overflow:'auto'},
        '.cm-content':{padding:'18px 0',caretColor:'#eee'},
        '.cm-line':{padding:'0 16px'},
        '.cm-gutters':{backgroundColor:'#080808',color:'#484848',borderRight:'1px solid #202020',paddingRight:'6px'},
        '.cm-activeLine,.cm-activeLineGutter':{backgroundColor:'#141414'},
        '.cm-selectionBackground,&.cm-focused .cm-selectionBackground':{backgroundColor:'#343434'},
        '&.cm-focused':{outline:'none'},'.cm-cursor':{borderLeftColor:'#eee'},
        '.cm-tooltip':{backgroundColor:'#161616',border:'1px solid #333',color:'#ddd'},
        '.cm-panels':{backgroundColor:'#111',color:'#ddd'},
      },{dark:true}),
    ]})});view.current=editor;
    return()=>{editor.destroy();view.current=null;};
  // The document is synchronized separately so edits preserve cursor and undo history.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[]);
  useEffect(()=>{const editor=view.current;if(editor&&editor.state.doc.toString()!==value)editor.dispatch({changes:{from:0,to:editor.state.doc.length,insert:value}});},[value]);
  return <div ref={host} className="python-code-host"/>;
}
