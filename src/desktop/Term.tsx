import {useEffect,useRef,useState} from 'react';
import {invoke,isTauri} from '@tauri-apps/api/core';
import {listen} from '@tauri-apps/api/event';
import {Terminal} from '@xterm/xterm';
import {FitAddon} from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import {Plus,X} from 'lucide-react';

/** The user's own shells, one ConPTY each in the folder this conversation works in.
 *
 * Terminals outlive the panel: the shell runs in the native process and its xterm element is kept
 * here, so closing and reopening the panel re-attaches the same session instead of killing it.
 */
type Shell={id:string;term:Terminal;fit:FitAddon;element:HTMLDivElement;title:string;exited?:number|null};
const shells=new Map<string,Shell[]>();// keyed by the folder the shells run in
let wired=false;
function wire(){
 if(wired)return;wired=true;
 void listen<{id:string;data:string}>('pty-data',e=>{for(const list of shells.values())for(const s of list)if(s.id===e.payload.id)s.term.write(e.payload.data)});
 void listen<{id:string;code:number|null}>('pty-exit',e=>{for(const list of shells.values())for(const s of list)if(s.id===e.payload.id){s.exited=e.payload.code??null;s.term.write(`\r\n\x1b[2m[process exited${e.payload.code!=null?` with code ${e.payload.code}`:''}]\x1b[0m\r\n`)}});
}
async function open(cwd:string):Promise<Shell>{
 const term=new Terminal({fontFamily:"'Cascadia Code',Consolas,monospace",fontSize:12,cursorBlink:true,scrollback:5000,allowProposedApi:true,theme:{background:'#0f0f11'}});
 const fit=new FitAddon();term.loadAddon(fit);
 const element=document.createElement('div');element.className='term-host';
 term.open(element);
 const id=await invoke<string>('pty_open',{cwd,cols:term.cols||100,rows:term.rows||30});
 term.onData(data=>{void invoke('pty_write',{id,data}).catch(()=>{})});
 term.onResize(({cols,rows})=>{void invoke('pty_resize',{id,cols,rows}).catch(()=>{})});
 return {id,term,fit,element,title:`Shell ${(shells.get(cwd)?.length||0)+1}`};
}

export function Term({cwd}:{cwd:string}){
 const host=useRef<HTMLDivElement>(null);
 const [list,setList]=useState<Shell[]>(()=>shells.get(cwd)||[]);
 const [active,setActive]=useState<string>(list[0]?.id||'');
 const [error,setError]=useState('');
 async function add(){try{wire();const s=await open(cwd);const next=[...(shells.get(cwd)||[]),s];shells.set(cwd,next);setList(next);setActive(s.id)}catch(e){setError(String((e as Error).message||e))}}
 function close(id:string){void invoke('pty_close',{id}).catch(()=>{});const s=(shells.get(cwd)||[]).find(x=>x.id===id);s?.term.dispose();const next=(shells.get(cwd)||[]).filter(x=>x.id!==id);shells.set(cwd,next);setList(next);if(active===id)setActive(next.at(-1)?.id||'')}
 useEffect(()=>{if(!list.length&&isTauri())void add()},[cwd]);
 useEffect(()=>{
  const el=host.current,s=list.find(x=>x.id===active);if(!el||!s)return;
  el.replaceChildren(s.element);
  const refit=()=>{try{s.fit.fit()}catch{/* not laid out yet */}};
  refit();s.term.focus();
  const ro=new ResizeObserver(refit);ro.observe(el);
  return()=>ro.disconnect();
 },[active,list]);
 if(!isTauri())return <p className="terminal-help">A terminal needs the desktop app. In the browser, run your own checks with the project check runner below.</p>;
 return <div className="term-panel">
  <div className="term-tabs">{list.map(s=><button key={s.id} className={s.id===active?'selected':''} onClick={()=>setActive(s.id)}><span>{s.title}{s.exited!=null?' · exited':''}</span><X size={11} aria-label={`Close ${s.title}`} onClick={e=>{e.stopPropagation();close(s.id)}}/></button>)}<button className="icon-button" title="New terminal" aria-label="New terminal" onClick={add}><Plus size={13}/></button></div>
  {error&&<p className="thread-error">{error}</p>}
  <div className="term-view" ref={host}/>
 </div>;
}
