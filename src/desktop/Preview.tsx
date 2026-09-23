import {useEffect,useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {RefreshCw,ExternalLink,Globe,ArrowRight,Play,Square,RotateCcw,Terminal,Skull} from 'lucide-react';
import {useWorkspace} from '../app/context';
import {api} from '../lib/api';

/** A dev server the project is running, shown in place. Ports are probed on loopback; any URL works too. */
type DevStatus={root:string;running:boolean;command?:string|null;pid?:number|null;port?:number|null;exit_code?:number|null;output?:string};
export function Preview({projectId,sessionId,devCommand}:{projectId:string;sessionId?:string;devCommand:string}){
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
  <div className="devserver-bar"><Terminal size={12}/><input aria-label="Dev server command" placeholder="npm run dev" value={command} onChange={e=>setCommand(e.target.value)} disabled={running}/>{running?<><button title="Restart" aria-label="Restart dev server" disabled={working!==''} onClick={()=>control('restart')}><RotateCcw size={12}/></button><button title="Stop" aria-label="Stop dev server" disabled={working!==''} onClick={()=>control('stop')}><Square size={12}/>Stop{dev.data?.port?` :${dev.data.port}`:''}</button></>:<button className="primary" title="Start the dev server in this session's folder" aria-label="Start dev server" disabled={working!==''||!command.trim()} onClick={()=>control('start')}><Play size={12}/>Start</button>}<button className="icon-button" title="Show output" aria-label="Show dev server output" onClick={()=>setShowLog(v=>!v)}><Terminal size={12}/></button></div>
  {devError&&<p className="thread-error">{devError}</p>}
  {showLog&&<pre className="devserver-log">{dev.data?.output||(dev.data?.exit_code!=null?`Exited with code ${dev.data.exit_code}.`:'No output yet.')}</pre>}
  <form className="preview-bar" onSubmit={e=>{e.preventDefault();go(draft)}}><Globe size={13}/><input aria-label="Preview URL" placeholder="http://localhost:3000" value={draft} onChange={e=>setDraft(e.target.value)}/><button className="icon-button" aria-label="Open URL" type="submit"><ArrowRight size={13}/></button><button className="icon-button" type="button" title="Reload" aria-label="Reload preview" onClick={()=>setNonce(n=>n+1)}><RefreshCw size={13}/></button><button className="icon-button" type="button" title="Open in your browser" aria-label="Open in your browser" disabled={!url} onClick={()=>window.open(url,'_blank')}><ExternalLink size={13}/></button></form>
  <div className="preview-ports">{ports.data?.ports.length?<>Listening: {ports.data.ports.map(p=><span key={p} className="port-chip"><button className={url.includes(`:${p}/`)||url.endsWith(`:${p}`)?'selected':''} onClick={()=>go(`http://localhost:${p}/`)}>:{p}</button><button className="icon-button" title={`Free port ${p}`} aria-label={`Free port ${p}`} onClick={()=>control('kill_port',p)}><Skull size={10}/></button></span>)}</>:<span>No dev server found on the usual ports. Start one above, or type a URL.</span>}</div>
  {url?<iframe key={`${url}#${nonce}`} ref={frame} className="preview-frame" src={url} title="Preview" sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-modals"/>:<p className="inspector-empty">Pick a port above or enter a URL.</p>}
 </div>;
}
