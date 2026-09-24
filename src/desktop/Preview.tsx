import {useEffect,useRef,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {RefreshCw,ExternalLink,Globe,ArrowRight,Play,Square,RotateCcw,Terminal,Skull,Bot,Eye} from 'lucide-react';
import type {Project} from '../types';
import {useWorkspace} from '../app/context';
import {api} from '../lib/api';
import {apiUrl} from '../lib/environment';

/** A dev server the project is running, shown in place. Ports are probed on loopback; any URL works too. */
type DevStatus={root:string;running:boolean;command?:string|null;pid?:number|null;port?:number|null;exit_code?:number|null;output?:string};
/** What the agent's browser saw: its screenshots, newest first, and the switch that gives agents a browser at all. */
function AgentBrowser({project,sessionId}:{project:Project;sessionId?:string}){
 const {tenant,path,notify}=useWorkspace();const client=useQueryClient();
 const q=sessionId?`?session_id=${sessionId}`:'';
 const [big,setBig]=useState<string|null>(null);
 // Shown at once, saved behind it; the project list catches up on its next read.
 const [local,setLocal]=useState({agent_browser:!!project.agent_browser,agent_browser_visible:!!project.agent_browser_visible});
 useEffect(()=>{setLocal({agent_browser:!!project.agent_browser,agent_browser_visible:!!project.agent_browser_visible})},[project.id,project.agent_browser,project.agent_browser_visible]);
 const shots=useQuery({queryKey:['tenant',tenant?.id,`/projects/${project.id}/browser-shots${q}`],enabled:!!tenant&&local.agent_browser,refetchInterval:6000,queryFn:({signal})=>api.get<{name:string;modified:string}[]>(path(`/projects/${project.id}/browser-shots${q}`),signal)});
 async function set(fields:{agent_browser?:boolean;agent_browser_visible?:boolean}){setLocal(v=>({...v,...fields}));try{await api.put(path(`/projects/${project.id}/settings`),fields);await client.invalidateQueries({queryKey:['tenant',tenant?.id]});notify(fields.agent_browser===false?'Agents no longer get a browser':'Saved')}catch(e){setLocal({agent_browser:!!project.agent_browser,agent_browser_visible:!!project.agent_browser_visible});notify((e as Error).message)}}
 const [token,setToken]=useState('');
 const [mode,setMode]=useState(project.agent_browser_mode||'fresh');
 useEffect(()=>setMode(project.agent_browser_mode||'fresh'),[project.id,project.agent_browser_mode]);
 async function saveMode(fields:{agent_browser_mode?:'fresh'|'mine';agent_browser_channel?:'chrome'|'msedge';agent_browser_token?:string}){
  if(fields.agent_browser_mode)setMode(fields.agent_browser_mode);
  try{await api.put(path(`/projects/${project.id}/settings`),fields);await client.invalidateQueries({queryKey:['tenant',tenant?.id]});notify('Saved')}catch(e){notify((e as Error).message)}
 }
 const src=(n:string)=>{const p=path(`/projects/${project.id}/browser-shot?name=${encodeURIComponent(n)}${sessionId?`&session_id=${sessionId}`:''}`);return apiUrl(p)};
 return <div className="agent-browser">
  <label className="toggle-row compact"><div><strong><Bot size={12}/> Let agents use a browser</strong><p>Each editing turn gets Playwright's browser tools: the agent opens the page, clicks through, reads the console and takes screenshots. Needs Node.js; the first turn downloads it.</p></div><input type="checkbox" role="switch" checked={local.agent_browser} onChange={e=>set({agent_browser:e.target.checked})}/></label>
  {local.agent_browser&&<div className="browser-choice" role="radiogroup" aria-label="Which browser">
   <label><input type="radio" name="browser-mode" checked={mode==='fresh'} onChange={()=>saveMode({agent_browser_mode:'fresh'})}/><span><strong>A fresh browser</strong><small>Starts clean each turn: nothing signed in, nothing of yours touched.</small></span></label>
   <label><input type="radio" name="browser-mode" checked={mode==='mine'} onChange={()=>saveMode({agent_browser_mode:'mine'})}/><span><strong>My own Chrome or Edge</strong><small>Your open tabs and the sites you are signed in to. Agents act in your real browser.</small></span></label>
  </div>}
  {local.agent_browser&&mode==='mine'&&<div className="browser-mine">
   <p className="muted small">It connects through Playwright's browser extension. Install it once in the browser you use, then keep that browser open while agents work: <a href="https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm" target="_blank" rel="noreferrer">Playwright extension</a> (Chrome Web Store; Edge installs it from there too).</p>
   <div className="pr-inline"><label>Browser</label><select aria-label="Your browser" value={project.agent_browser_channel||'chrome'} onChange={e=>saveMode({agent_browser_channel:e.target.value as 'chrome'|'msedge'})}><option value="chrome">Chrome</option><option value="msedge">Microsoft Edge</option></select></div>
   <div className="pr-inline"><label>Token</label><input type="password" aria-label="Extension token" placeholder={project.agent_browser_token_set?'Saved. Paste a new one to replace it':'Optional: the token from the extension'} value={token} onChange={e=>setToken(e.target.value)}/><button disabled={!token.trim()} onClick={()=>saveMode({agent_browser_token:token.trim()}).then(()=>setToken(''))}>Save</button>{project.agent_browser_token_set&&<button className="linkish" onClick={()=>saveMode({agent_browser_token:''})}>Forget</button>}</div>
   <p className="muted small">Without a token, the extension asks you to pick a tab and allow the connection at the start of each turn. With its token saved here, agents connect on their own.</p>
  </div>}
  {local.agent_browser&&mode==='fresh'&&<label className="toggle-row compact"><div><strong><Eye size={12}/> Show the agent's browser window</strong><p>Watch it work instead of running it hidden.</p></div><input type="checkbox" role="switch" checked={local.agent_browser_visible} onChange={e=>set({agent_browser_visible:e.target.checked})}/></label>}
  {local.agent_browser&&(shots.data?.length?<div className="agent-shots">{shots.data.slice(0,12).map(s=><button key={s.name} title={`${s.name} · ${new Date(s.modified).toLocaleTimeString()}`} onClick={()=>setBig(s.name)}><img src={src(s.name)} alt={s.name} loading="lazy"/></button>)}</div>:<p className="muted small">The agent's screenshots appear here.</p>)}
  {big&&<div className="shot-viewer" role="dialog" aria-label={big} onClick={()=>setBig(null)}><img src={src(big)} alt={big}/><span>{big}</span></div>}
 </div>;
}

export function Preview({projectId,sessionId,devCommand,project}:{projectId:string;sessionId?:string;devCommand:string;project?:Project}){
 const {tenant,path,notify}=useWorkspace();
 const q=sessionId?`?session_id=${sessionId}`:'';
 const dev=useQuery({queryKey:['tenant',tenant?.id,`/projects/${projectId}/devserver${q}`],enabled:!!tenant,refetchInterval:4000,queryFn:({signal})=>api.get<DevStatus>(path(`/projects/${projectId}/devserver${q}`),signal)});
 const [command,setCommand]=useState(devCommand),[showLog,setShowLog]=useState(false),[working,setWorking]=useState(''),[devError,setDevError]=useState('');
 useEffect(()=>{if(dev.data?.command&&!command)setCommand(dev.data.command)},[dev.data?.command]);
 async function control(action:'start'|'stop'|'restart'|'kill_port',port?:number){setWorking(action);setDevError('');try{await api.post(path(`/projects/${projectId}/devserver${q}`),{action,command:action==='stop'||action==='kill_port'?null:command.trim()||null,port:port??null});await dev.refetch();if(action==='kill_port')notify(`Port ${port} freed`)}catch(e){setDevError((e as Error).message)}finally{setWorking('')}}
 const ports=useQuery({queryKey:['tenant',tenant?.id,`/projects/${projectId}/ports`],enabled:!!tenant,refetchInterval:8000,queryFn:({signal})=>api.get<{ports:number[]}>(path(`/projects/${projectId}/ports`),signal)});
 const key=`frontier.preview.${projectId}`;
 const [url,setUrl]=useState(()=>localStorage.getItem(key)||''),[draft,setDraft]=useState(''),[nonce,setNonce]=useState(0);
 const frame=useRef<HTMLIFrameElement>(null);
 useEffect(()=>{setDraft(url);localStorage.setItem(key,url)},[url]);
 useEffect(()=>{if(!url&&ports.data?.ports.length)setUrl(`http://localhost:${ports.data.ports[0]}/`)},[ports.data]);
 useEffect(()=>{const p=dev.data?.port;if(dev.data?.running&&p&&!url.includes(`:${p}`))setUrl(`http://localhost:${p}/`)},[dev.data?.port,dev.data?.running]);
 const go=(u:string)=>{const v=u.trim();if(!v)return;setUrl(/^https?:\/\//.test(v)?v:`http://${v}`);setNonce(n=>n+1)};
 const running=!!dev.data?.running;
 return <div className="preview-panel">
  {project&&<AgentBrowser project={project} sessionId={sessionId}/>}
  <div className="devserver-bar"><Terminal size={12}/><input aria-label="Dev server command" placeholder="npm run dev" value={command} onChange={e=>setCommand(e.target.value)} disabled={running}/>{running?<><button title="Restart" aria-label="Restart dev server" disabled={working!==''} onClick={()=>control('restart')}><RotateCcw size={12}/></button><button title="Stop" aria-label="Stop dev server" disabled={working!==''} onClick={()=>control('stop')}><Square size={12}/>Stop{dev.data?.port?` :${dev.data.port}`:''}</button></>:<button className="primary" title="Start the dev server in this session's folder" aria-label="Start dev server" disabled={working!==''||!command.trim()} onClick={()=>control('start')}><Play size={12}/>Start</button>}<button className="icon-button" title="Show output" aria-label="Show dev server output" onClick={()=>setShowLog(v=>!v)}><Terminal size={12}/></button></div>
  {devError&&<p className="thread-error">{devError}</p>}
  {showLog&&<pre className="devserver-log">{dev.data?.output||(dev.data?.exit_code!=null?`Exited with code ${dev.data.exit_code}.`:'No output yet.')}</pre>}
  <form className="preview-bar" onSubmit={e=>{e.preventDefault();go(draft)}}><Globe size={13}/><input aria-label="Preview URL" placeholder="http://localhost:3000" value={draft} onChange={e=>setDraft(e.target.value)}/><button className="icon-button" aria-label="Open URL" type="submit"><ArrowRight size={13}/></button><button className="icon-button" type="button" title="Reload" aria-label="Reload preview" onClick={()=>setNonce(n=>n+1)}><RefreshCw size={13}/></button><button className="icon-button" type="button" title="Open in your browser" aria-label="Open in your browser" disabled={!url} onClick={()=>window.open(url,'_blank')}><ExternalLink size={13}/></button></form>
  <div className="preview-ports">{ports.data?.ports.length?<>Listening: {ports.data.ports.map(p=><span key={p} className="port-chip"><button className={url.includes(`:${p}/`)||url.endsWith(`:${p}`)?'selected':''} onClick={()=>go(`http://localhost:${p}/`)}>:{p}</button><button className="icon-button" title={`Free port ${p}`} aria-label={`Free port ${p}`} onClick={()=>control('kill_port',p)}><Skull size={10}/></button></span>)}</>:<span>No dev server found on the usual ports. Start one above, or type a URL.</span>}</div>
  {url?<iframe key={`${url}#${nonce}`} ref={frame} className="preview-frame" src={url} title="Preview" sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-modals"/>:<p className="inspector-empty">Pick a port above or enter a URL.</p>}
 </div>;
}
