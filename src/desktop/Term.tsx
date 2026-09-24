import {useEffect,useRef,useState} from 'react';
import {environment,onThisComputer} from '../lib/environment';
import {invoke} from '@tauri-apps/api/core';
import {listen} from '@tauri-apps/api/event';
import {Terminal} from '@xterm/xterm';
import {FitAddon} from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import {Plus,X,MessageSquarePlus,Bot} from 'lucide-react';
import {useWorkspace,useResource} from '../app/context';
import {CLI_NAMES} from '../types';
import type {ContextChip} from '../types';

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

export function Term(props:{cwd:string;onChip?:(chip:ContextChip)=>void;projectId?:string;sessionId?:string;agents?:{provider:string;name:string}[]}){
 // The terminal is a shell on the computer in front of you; a project on another machine is not there.
 if(!onThisComputer)return <p className="inspector-empty">The terminal runs on this computer, and this project is on {environment?.name}. Ask the agent to run commands there, or use a terminal on that machine.</p>;
 return <LocalTerm {...props}/>;
}

function LocalTerm({cwd,onChip,projectId,sessionId,agents=[]}:{cwd:string;onChip?:(chip:ContextChip)=>void;projectId?:string;sessionId?:string;agents?:{provider:string;name:string}[]}){
 const host=useRef<HTMLDivElement>(null);
 const {notify}=useWorkspace();
 const env=useResource<{kind:string|null;activate?:string|null;note?:string}>(`/projects/${projectId}/environment${sessionId?`?session_id=${sessionId}`:''}`,!!projectId);
 const [agentMenu,setAgentMenu]=useState(false);
 function typeInto(text:string){const s=list.find(x=>x.id===active);if(!s){setError('Open a terminal first.');return}void invoke('pty_write',{id:s.id,data:text+'\r'}).catch(()=>{});s.term.focus()}
 const [list,setList]=useState<Shell[]>(()=>shells.get(cwd)||[]);
 const [active,setActive]=useState<string>(list[0]?.id||'');
 const [error,setError]=useState('');
 async function add(){try{wire();const s=await open(cwd);const next=[...(shells.get(cwd)||[]),s];shells.set(cwd,next);setList(next);setActive(s.id);if(env.data?.activate){setTimeout(()=>{void invoke('pty_write',{id:s.id,data:env.data!.activate+'\r'}).catch(()=>{})},600)}}catch(e){setError(String((e as Error).message||e))}}
 function close(id:string){void invoke('pty_close',{id}).catch(()=>{});const s=(shells.get(cwd)||[]).find(x=>x.id===id);s?.term.dispose();const next=(shells.get(cwd)||[]).filter(x=>x.id!==id);shells.set(cwd,next);setList(next);if(active===id)setActive(next.at(-1)?.id||'')}
 useEffect(()=>{if(!list.length)void add()},[cwd]);
 useEffect(()=>{
  const el=host.current,s=list.find(x=>x.id===active);if(!el||!s)return;
  el.replaceChildren(s.element);
  const refit=()=>{try{s.fit.fit()}catch{/* not laid out yet */}};
  refit();s.term.focus();
  const ro=new ResizeObserver(refit);ro.observe(el);
  return()=>ro.disconnect();
 },[active,list]);
 return <div className="term-panel">
  <div className="term-tabs">{list.map(s=><button key={s.id} className={s.id===active?'selected':''} onClick={()=>setActive(s.id)}><span>{s.title}{s.exited!=null?' · exited':''}</span><X size={11} aria-label={`Close ${s.title}`} onClick={e=>{e.stopPropagation();close(s.id)}}/></button>)}<button className="icon-button" title="New terminal" aria-label="New terminal" onClick={add}><Plus size={13}/></button>{agents.length>0&&<span className="term-agent"><button className="icon-button" title="Run an agent's own command line tool here, interactively" aria-label="Run an agent here" onClick={()=>setAgentMenu(v=>!v)}><Bot size={13}/></button>{agentMenu&&<><div className="menu-backdrop" onMouseDown={()=>setAgentMenu(false)}/><div className="context-menu term-agent-menu" role="menu">{[...new Set(agents.map(a=>a.provider))].map(p=><button key={p} type="button" role="menuitem" onClick={()=>{setAgentMenu(false);typeInto(CLI_NAMES[p]||p);notify(`Running ${CLI_NAMES[p]||p} in the terminal`)}}><Bot size={12}/>{CLI_NAMES[p]||p}</button>)}</div></>}</span>}{onChip&&<button className="icon-button" title="Send the selected terminal text to the prompt" aria-label="Reference terminal selection in the prompt" onClick={()=>{const s=list.find(x=>x.id===active);const text=s?.term.getSelection().trim();if(!text){setError('Select some terminal output first.');return}setError('');onChip({kind:'terminal',text:text.slice(0,20000),label:`${s!.title} output`})}}><MessageSquarePlus size={13}/></button>}</div>
  {error&&<p className="thread-error">{error}</p>}
  <div className="term-view" ref={host}/>
 </div>;
}
